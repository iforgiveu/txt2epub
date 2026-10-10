# -*- coding: utf-8 -*-
"""
preview_titles.py —— txt2epub 标题规则可视化预览/调试工具 v14

v14 更新（工具栏入口重组：把原来的两个"重新载入"拆分为更明确的两个动作）：
1. 原按钮「重新检测规则」→ 改为「重新读取 txt」(reload_txt)：
   按 self.file_path 从磁盘重读当前文件内容并重新判定，
   用于在外部编辑器修改 txt 后快速刷新，无需重新选择文件；
2. 原按钮「重载规则模块」→ 改为「重新读取规则」(reload_rules)：
   importlib.reload(txt2epub) 丢弃界面上未写回源码的临时规则修改，
   恢复为 txt2epub.py 文件中的原始定义，并同步规则列表/编辑器/源码区；
3. reload_txt 重读后行号可能错位，默认清空手动标记（与 open_file 一致）；
4. reload_rules 会把规则编辑器表单同步为列表第一条，避免残留已删除规则的旧内容。

v13 更新（修复"逐行预览里复制/剪切/粘贴后莫名多出一列行号"）：
1. 问题根源：旧版剪切/粘贴通过 event_generate 让 Text 控件直接编辑
   "行号+标签+正文" 的渲染文本，再把控件文本读回 self.lines，
   导致 self.lines 被渲染前缀污染，重渲染时行号套行号，且标记被迫清空；
2. 修复：复制/剪切/粘贴全部改为在数据层（self.lines）操作，预览控件
   只做只读渲染，不再从控件读回任何内容；
3. 渲染时记录每行前缀（行号栏+判定标签）长度 _prefix_len，用于把控件
   里的选区/光标位置精确换算回源文件 (行, 列)；
4. 复制到剪贴板的内容是纯源文本，天然不含行号与 [章·规则名] 标签；
5. 键盘 Ctrl+C/X/V 也被接管到同一数据层路径（bind 虚拟事件并 return 'break'），
   与右键菜单行为一致。

v12 更新（与 txt2epub.py 现有实现进一步对齐）：
1. rebuild_compiled_rules 为 TITLE_RULES 里新出现的 series key 自动补齐
   _SERIES_RULES 条目，避免用户在预览器里新增带 series 的规则后
   is_series_listing() 抛 KeyError；
2. detect() 里 stats 提取改为显式判断 v['hit'] > 0，并累计到 self.rule_stats，
   与 txt2epub 控制台报告同口径；
3. 状态栏追加「规则命中: name(命中/过滤), ...」明细展示；
4. 保留旧版 txt2epub（detect_title 无 stats）的回退路径。

v11 更新（与 txt2epub.py 现有实现对齐）：
1. 逐行判定改用 txt2epub.detect_title(..., stats) 统一入口，
   同时拿到「是否标题 / 层级 / 命中规则名」，与转换逻辑单一真相源；
2. 预览同步 IGNORE_INDENT_LINES / _LEADING_WS 行为：
   行首带缩进的正文行不再被误判为标题（手动"添加"标记仍可强制覆盖）；
3. 导出 EPUB 时同步打补丁 _LEADING_WS，使缩进行上的手动"添加"标记
   在正式转换中同样生效（原版流程会在 detect_title 之前跳过缩进行）；
4. "当前 TITLE_RULES 源码"生成时不再丢失 series 字段。
"""

import os, re, time, tempfile, colorsys, tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import txt2epub

FONT_FAMILY, MONO_FAMILY = 'Microsoft YaHei', 'Consolas'
DEFAULT_PREVIEW_SIZE, DEFAULT_TREE_SIZE, BASE_FONT_SIZE = 18, 16, 11
ZOOM_MIN, ZOOM_MAX = 10, 40

LEVEL_BASE = {0: '#E53935', 1: '#1E88E5', 2: '#43A047'}
# 同层级内每条规则的色相偏移步进（度），越大区分越明显（>40 有跨色系混淆风险）
HUE_STEP_DEG = 20.0
# 明度波动序列：轻微深浅变化辅助区分，正值勿超 0.35（白底看不清）
SHADE_FACTORS = [0.0, 0.10, 0.16, 0.22, 0.28, 0.34, -0.15, 0.12]
LV_NAME = {0: '部', 1: '卷', 2: '章'}

FILTER_ADDED, FILTER_REMOVED = '★手动添加的标题', '★被移除的原标题'
EXPORT_FN_NAME = 'process_file_native_with_toc'  # 签名 (base_dir, filename)


def rebuild_compiled_rules():
    """重建 COMPILED_RULES，并为新出现的 series key 自动补齐 _SERIES_RULES 条目"""
    compiled = []
    for r in txt2epub.TITLE_RULES:
        compiled.append((r['name'], r['level'], r['mode'], r.get('series'),
                         re.compile(r['pattern'], re.VERBOSE)))
    txt2epub.COMPILED_RULES = compiled
    by_name = {c[0]: c[4] for c in compiled}

    # ---- 已有条目：line_re 为 None 时用规则自身正则兜底 ----
    for key, entry in list(txt2epub._SERIES_RULES.items()):
        line_re, item_re = entry
        if line_re is None and key in by_name:
            txt2epub._SERIES_RULES[key] = (by_name[key], item_re)

    # ---- ★ v12：TITLE_RULES 用到的 series key 若在 _SERIES_RULES 里缺失，
    # 用规则自身的编译正则临时补齐，避免 is_series_listing 抛 KeyError。
    # 这样即便在预览器里加了新 series，也能跑通（行为近似：只用规则自身正则
    # 做「同行多列举 / 相邻行同类」判断）。真正的过滤模式仍应由 txt2epub.py 定义。
    for name, _lv, _mode, series_key, rx in compiled:
        if series_key and series_key not in txt2epub._SERIES_RULES:
            txt2epub._SERIES_RULES[series_key] = (rx, rx)


def read_txt(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, encoding='gbk') as f:
            return f.read()


def _tint(hex_color, idx):
    """同层级第 idx 条规则：色相偏移 idx*HUE_STEP_DEG，明度按 SHADE_FACTORS 轻微波动"""
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
    hue = (hue + idx * HUE_STEP_DEG / 360.0) % 1.0  # 色相偏移，自动回绕
    factor = SHADE_FACTORS[idx % len(SHADE_FACTORS)]  # 轻微明度波动
    val = val + (1.0 - val) * factor if factor >= 0 else val * (1 + factor)
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return f'#{round(r*255):02x}{round(g*255):02x}{round(b*255):02x}'


def build_color_map(results):
    names_by_level = {0: set(), 1: set(), 2: set()}
    for is_t, lv, name in results:
        if is_t and name:
            names_by_level[lv].add(name)
    cmap = {}
    for lv, names in names_by_level.items():
        for idx, name in enumerate(sorted(names)):
            cmap[(lv, name)] = _tint(LEVEL_BASE[lv], idx)
    return cmap


def _call_series_filter(series_key, i, lines):
    """旧版 txt2epub 回退路径使用；新版直接走 detect_title 的 stats。"""
    fn = getattr(txt2epub, 'is_series_listing', None)
    if fn is None:
        return False
    for args in ((i, lines, series_key), (i, lines)):
        try:
            return bool(fn(*args))
        except TypeError:
            continue
        except Exception:
            return False
    return False


def find_source_rule(line_strip, i, lines):
    """★ v11 起仅作为旧版 txt2epub（detect_title 无 stats 参数）的回退方案"""
    for name, _lv, mode, series_key, rx in txt2epub.COMPILED_RULES:
        try:
            hit = rx.search(line_strip) if mode == 'search' else rx.match(line_strip)
        except Exception:
            continue
        if hit and not (series_key and _call_series_filter(series_key, i, lines)):
            return name
    return None


class PreviewApp:
    def __init__(self, root):
        self.root = root
        root.title('txt2epub 标题规则预览器')
        root.geometry('1600x900'); root.minsize(1200, 720)

        self.lines, self.results, self.orig_flags = [], [], []
        self.color_map = {}
        self.rule_stats = {}      # ★ v12：规则名 -> {'hit': N, 'filtered': M}
        self.title_marks = {}     # 行号 -> ('add', level) / ('remove', None)
        self._mark_undo = []      # 撤销栈（标记快照）
        self.preview_size, self.tree_size = DEFAULT_PREVIEW_SIZE, DEFAULT_TREE_SIZE
        self.filter_var = tk.StringVar(value='全部')
        self.show_source_var = tk.BooleanVar(value=True)
        self._zoom_job = None
        self._tree_item_path, self._tree_item_line = {}, {}
        self._last_preview_line = self._last_tree_line = None
        self._prefix_len = []     # ★ v13：每行渲染前缀（行号+标签）长度，用于坐标换算

        tkfont.nametofont('TkDefaultFont').configure(family=FONT_FAMILY, size=11)
        tkfont.nametofont('TkTextFont').configure(family=FONT_FAMILY, size=11)

        self._setup_styles(); self._build_ui(); self._create_context_menus()
        rebuild_compiled_rules(); self.refresh_rule_list(); self.refresh_rules_code()

    # ==================== 样式与缩放 ====================
    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('.', font=(FONT_FAMILY, 11))
        style.configure('TButton', font=(FONT_FAMILY, 11), padding=6)
        style.configure('Small.TButton', font=(FONT_FAMILY, 11), padding=6)
        style.configure('Treeview', rowheight=int(11 * 2.4), font=(FONT_FAMILY, 11))
        style.configure('Treeview.Heading', font=(FONT_FAMILY, 11, 'bold'))
        self._apply_tree_zoom()

    def _apply_tree_zoom(self):
        f = tkfont.Font(family=FONT_FAMILY, size=self.tree_size)
        style = ttk.Style()
        style.configure('Zoom.Treeview', rowheight=f.metrics('linespace') + 10,
                        font=(FONT_FAMILY, self.tree_size))
        style.configure('Zoom.Treeview.Heading', font=(FONT_FAMILY, self.tree_size, 'bold'))

    def _apply_preview_zoom(self):
        self.preview.configure(font=(FONT_FAMILY, self.preview_size))
        for (lv, name), _c in self.color_map.items():
            self.preview.tag_configure(f't{lv}_{name}',
                                       font=(FONT_FAMILY, self.preview_size, 'bold'))
        self.preview.tag_configure('body', font=(FONT_FAMILY, self.preview_size))

    # ==================== UI ====================
    def _build_ui(self):
        bar = ttk.Frame(self.root, padding=(8, 6)); bar.pack(fill='x')
        # ★ v14：把原来的「重新检测规则」「重载规则模块」改为
        #   「重新读取 txt」（从磁盘重读当前文件）
        #   「重新读取规则」（重载 txt2epub 模块，恢复源码中的规则定义）
        for text, cmd in (('打开 txt 文件', self.open_file),
                          ('重新读取 txt', self.reload_txt),
                          ('重新读取规则', self.reload_rules)):
            ttk.Button(bar, text=text, command=cmd).pack(side='left')

        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Button(bar, text='A- 缩小', command=lambda: self.zoom(-1)).pack(side='left', padx=2)
        ttk.Button(bar, text='A+ 放大', command=lambda: self.zoom(1)).pack(side='left', padx=2)
        self.zoom_label = ttk.Label(bar, text=f' 字号: {self.preview_size}')
        self.zoom_label.pack(side='left', padx=4)

        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Label(bar, text='章节树筛选:').pack(side='left')
        self.filter_combo = ttk.Combobox(bar, textvariable=self.filter_var,
                                         state='readonly', width=int(16 * 1.3))
        self.filter_combo.pack(side='left', padx=4)
        self.filter_combo.bind('<<ComboboxSelected>>', lambda e: self.render_tree())

        ttk.Checkbutton(bar, text='显示判定来源', variable=self.show_source_var,
                        command=self.detect_and_render).pack(side='left', padx=8)

        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Button(bar, text='↶ 撤销标记', command=self.undo_mark).pack(side='left', padx=2)
        ttk.Button(bar, text='📦 导出 EPUB', command=self.export_epub).pack(side='left', padx=6)

        k = 18 / 11
        paned = ttk.Panedwindow(self.root, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=8, pady=6)
        self.paned = paned

        # ---- 左：规则编辑器 ----
        left = ttk.Labelframe(paned, text='标题规则（优先级 = 从上到下）', padding=8)
        paned.add(left, weight=2)
        cols, headers = ('name', 'level', 'mode', 'series'), ('规则名', '层级', '模式', '过滤key')
        widths = (int(130 * k), int(60 * k), int(90 * k), int(100 * k))
        self.rule_tree = ttk.Treeview(left, columns=cols, show='headings', height=9)
        for c, h, w in zip(cols, headers, widths):
            self.rule_tree.heading(c, text=h); self.rule_tree.column(c, width=w, anchor='center')
        self.rule_tree.pack(fill='x')
        self.rule_tree.bind('<<TreeviewSelect>>', self.on_rule_selected)

        btns = ttk.Frame(left); btns.pack(fill='x', pady=4)
        ttk.Button(btns, text='↑ 上移', command=lambda: self.move_rule(-1)).pack(side='left', padx=3)
        ttk.Button(btns, text='↓ 下移', command=lambda: self.move_rule(1)).pack(side='left', padx=3)

        form = ttk.Frame(left); form.pack(fill='x', pady=6)
        self.var_name, self.var_level = tk.StringVar(), tk.StringVar(value='2')
        self.var_mode, self.var_series = tk.StringVar(value='match'), tk.StringVar()
        for label, var, w in (('规则名', self.var_name, int(16 * k)),
                              ('层级', self.var_level, int(5 * k)),
                              ('模式', self.var_mode, int(9 * k)),
                              ('过滤key', self.var_series, int(13 * k))):
            f = ttk.Frame(form); f.pack(side='left', padx=4)
            ttk.Label(f, text=label).pack(anchor='w')
            ttk.Entry(f, textvariable=var, width=w, font=(FONT_FAMILY, 11)).pack()
        ttk.Label(form, text='0=部 1=卷 2=章\nsearch=任意 match=行首',
                  font=(FONT_FAMILY, 10), foreground='#666').pack(side='left', padx=8)

        ttk.Label(left, text='正则 pattern（支持 re.VERBOSE 语法）').pack(anchor='w', pady=(6, 0))
        self.pattern_text = tk.Text(left, height=int(7 * k), font=(MONO_FAMILY, 11), wrap='none')
        self.pattern_text.pack(fill='x')

        ops = ttk.Frame(left); ops.pack(fill='x', pady=6)
        for text, cmd in (('保存修改', self.save_rule), ('新增', self.add_rule),
                          ('删除', self.del_rule), ('应用并预览', self.apply_rules)):
            ttk.Button(ops, text=text, command=cmd).pack(side='left', padx=3)

        ttk.Label(left, text='当前 TITLE_RULES 源码（可复制回 txt2epub.py）').pack(anchor='w', pady=(10, 0))
        self.code_text = tk.Text(left, height=int(10 * k), font=(MONO_FAMILY, 11),
                                 background='#f5f5f5', wrap='none')
        self.code_text.pack(fill='both', expand=True)

        # ---- 右：预览 / 章节树 ----
        right = ttk.Notebook(paned); paned.add(right, weight=3); self.right_nb = right

        self.frame_preview = ttk.Frame(right); right.add(self.frame_preview, text=' 逐行预览 ')
        self.preview = tk.Text(self.frame_preview, wrap='none', undo=False,
                               font=(FONT_FAMILY, self.preview_size))
        ysb = ttk.Scrollbar(self.frame_preview, orient='vertical', command=self.preview.yview)
        xsb = ttk.Scrollbar(self.frame_preview, orient='horizontal', command=self.preview.xview)
        self.preview.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.preview.pack(side='left', fill='both', expand=True)
        ysb.pack(side='right', fill='y'); xsb.pack(side='bottom', fill='x')
        self.preview.tag_configure('body', foreground='#757575')

        frame2 = ttk.Frame(right); right.add(frame2, text=' 章节树结构 ')
        self.chap_tree = ttk.Treeview(frame2, show='tree', style='Zoom.Treeview')
        csb = ttk.Scrollbar(frame2, orient='vertical', command=self.chap_tree.yview)
        self.chap_tree.configure(yscrollcommand=csb.set)
        self.chap_tree.pack(side='left', fill='both', expand=True); csb.pack(side='right', fill='y')

        self.status_var = tk.StringVar(value='就绪')
        ttk.Label(self.root, textvariable=self.status_var, relief='sunken', anchor='w',
                  padding=(6, 3), font=(FONT_FAMILY, 11)).pack(fill='x', side='bottom')

        self.root.after(100, lambda: self.paned.sashpos(0, int(540 * k)))

        for w in (self.preview, self.chap_tree, self.rule_tree):
            w.bind('<Control-MouseWheel>', self._on_ctrl_wheel)
        self.preview.bind('<Button-3>', self._on_preview_right_click)
        # ★ v13：接管 Ctrl+C/X/V 虚拟事件，阻止 Text 默认绑定直接编辑渲染文本
        # （实例绑定先于类绑定执行，return 'break' 阻止默认的控件级编辑）
        for seq, op in (('<<Copy>>', 'copy'), ('<<Cut>>', 'cut'), ('<<Paste>>', 'paste')):
            self.preview.bind(seq, lambda e, op=op: (self._clip(op), 'break')[1])
        self.chap_tree.bind('<Button-3>', self._on_tree_right_click)
        self.root.bind('<Control-z>', lambda e: self.undo_mark())

    # ==================== 右键菜单 ====================
    def _create_context_menus(self):
        # 预览右键菜单（菜单项索引：0复制 1剪切 2粘贴 | 4~6添加 | 8移除 9撤销该行 11撤销上一次）
        self.preview_menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, 11))
        self.preview_menu.add_command(label='复制', command=lambda: self._clip('copy'))
        self.preview_menu.add_command(label='剪切', command=lambda: self._clip('cut'))
        self.preview_menu.add_command(label='粘贴', command=lambda: self._clip('paste'))
        self.preview_menu.add_separator()
        for lv in (0, 1, 2):
            self.preview_menu.add_command(
                label=f'添加为 {LV_NAME[lv]}',
                command=lambda lv=lv: self._set_mark_from(self._last_preview_line, lv))
        self.preview_menu.add_separator()
        self.preview_menu.add_command(
            label='移除当前行标题资格',
            command=lambda: self._set_mark_from(self._last_preview_line, 'remove'))
        self.preview_menu.add_command(
            label='撤销当前行标记',
            command=lambda: self._set_mark_from(self._last_preview_line, None))
        self.preview_menu.add_separator()
        self.preview_menu.add_command(label='↶ 撤销上一次标记', command=self.undo_mark)

        self.tree_menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, 11))
        self.tree_menu.add_command(label='跳转到逐行预览所在行', command=self.jump_to_preview)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(
            label='移除该标题资格',
            command=lambda: self._set_mark_from(self._last_tree_line, 'remove'))
        self.tree_menu.add_command(
            label='撤销该行标记',
            command=lambda: self._set_mark_from(self._last_tree_line, None))
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label='↶ 撤销上一次标记', command=self.undo_mark)

    # ==================== 标记核心：快照 → 修改 → 重判定渲染 ====================
    def _set_mark_from(self, i, kind):
        """kind: 0/1/2=添加为对应层级, 'remove'=移除标题资格, None=清除该行标记"""
        if i is None:
            return
        mark = None if kind is None else \
            ('remove', None) if kind == 'remove' else ('add', kind)
        self._set_mark(i, mark)

    def _push_mark_undo(self):
        self._mark_undo.append(dict(self.title_marks))
        if len(self._mark_undo) > 100:
            self._mark_undo.pop(0)

    def _set_mark(self, i, mark):
        self._push_mark_undo()
        if mark is None:
            self.title_marks.pop(i, None)
        else:
            self.title_marks[i] = mark
        self.detect_and_render()
        n_add = sum(1 for v in self.title_marks.values() if v[0] == 'add')
        n_rm = sum(1 for v in self.title_marks.values() if v[0] == 'remove')
        self.status_var.set(f'标记已更新：共 {len(self.title_marks)} 处（+{n_add} / -{n_rm}），Ctrl+Z 可撤销')

    def undo_mark(self):
        if not self._mark_undo:
            self.status_var.set('没有可撤销的标记'); return
        self.title_marks = self._mark_undo.pop()
        self.detect_and_render()
        self.status_var.set(f'已撤销标记，剩余 {len(self.title_marks)} 处')

    # ==================== 剪贴板（★ v13：数据层操作，预览只读渲染） ====================
    def _widget_to_src(self, index):
        """把预览控件位置换算回 (源行号, 源列)；落在行号栏/标签列时映射到行首"""
        try:
            row, col = self.preview.index(index).split('.')
        except Exception:
            return None
        i, col = int(row) - 1, int(col)
        if not (0 <= i < len(self.lines)):
            return None
        if i < len(self._prefix_len):
            col = max(0, col - self._prefix_len[i])  # 前缀区一律视为行首
        disp = self.lines[i].rstrip('\r')
        return i, min(col, len(disp))

    def _get_sel_src_range(self):
        """把控件选区换算回源文件坐标 (i1, c1, i2, c2)；无选区返回 None"""
        rng = self.preview.tag_ranges('sel')
        if not rng:
            return None
        a, b = self._widget_to_src(rng[0]), self._widget_to_src(rng[1])
        if a is None or b is None:
            return None
        if a > b:
            a, b = b, a
        return a[0], a[1], b[0], b[1]

    def _src_text_between(self, i1, c1, i2, c2):
        """取源文本 [i1,c1) ~ [i2,c2) 的纯内容（天然不含行号/标签）"""
        if i1 == i2:
            return self.lines[i1].rstrip('\r')[c1:c2]
        parts = [self.lines[i1].rstrip('\r')[c1:]]
        parts.extend(self.lines[k].rstrip('\r') for k in range(i1 + 1, i2))
        parts.append(self.lines[i2].rstrip('\r')[:c2])
        return '\n'.join(parts)

    def _clip(self, op):
        if not self.lines:
            return
        sel = self._get_sel_src_range()

        if op == 'copy':
            if sel is None:
                # 无选区：复制鼠标所在行整行（取源行原文）
                i = self._last_preview_line
                if i is None or not (0 <= i < len(self.lines)):
                    return
                text = self.lines[i].rstrip('\r')
            else:
                text = self._src_text_between(*sel)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set('已复制到剪贴板（不含行号与标签）')
            return

        if op == 'cut':
            if sel is None:
                return  # 剪切必须先选中
            i1, c1, i2, c2 = sel
            self.root.clipboard_clear()
            self.root.clipboard_append(self._src_text_between(i1, c1, i2, c2))
            # 首尾行合并成新行（多行剪切 = 合并两端剩余部分）
            if i1 == i2:
                d = self.lines[i1].rstrip('\r')
                nd = d[:c1] + d[c2:]
                if self.lines[i1].endswith('\r') and nd:
                    nd += '\r'
            else:
                d1, d2 = self.lines[i1].rstrip('\r'), self.lines[i2].rstrip('\r')
                nd = d1[:c1] + d2[c2:]
                if self.lines[i2].endswith('\r') and nd:
                    nd += '\r'
            self.lines[i1:i2 + 1] = [nd]
            self._after_src_edit()
            return

        if op == 'paste':
            try:
                text = self.root.clipboard_get()
            except Exception:
                return
            pos = self._widget_to_src('insert')
            if pos is None:
                return
            i, c = pos
            d = self.lines[i].rstrip('\r')
            cr = '\r' if self.lines[i].endswith('\r') else ''
            parts = text.split('\n')
            if len(parts) == 1:
                self.lines[i] = d[:c] + parts[0] + d[c:] + cr
            else:
                self.lines[i:i + 1] = ([d[:c] + parts[0]] + parts[1:-1]
                                       + [parts[-1] + d[c:] + cr])
            self._after_src_edit()

    def _after_src_edit(self):
        """★ v13：数据层编辑完成后的统一收尾（重判定 + 重渲染 + 状态提示）"""
        note = ''
        if self.title_marks:
            # 行号整体位移，按行号索引的标记无法对齐，清空并提示
            self.title_marks.clear(); self._mark_undo.clear()
            note = '（行号已变化，手动标记已清空）'
        self.detect_and_render()
        self.status_var.set(f'内容已编辑，共 {len(self.lines)} 行{note}')

    # ==================== 右键事件 ====================
    def _on_preview_right_click(self, event):
        self.preview.mark_set(tk.INSERT, f'@{event.x},{event.y}')
        i = int(self.preview.index('insert').split('.')[0]) - 1
        self._last_preview_line = i if 0 <= i < len(self.lines) else None  # 显示行号=源行号

        i = self._last_preview_line
        m = self.title_marks.get(i) if i is not None else None
        has_sel = bool(self.preview.tag_ranges('sel'))
        try:
            can_paste = bool(self.root.clipboard_get())
        except Exception:
            can_paste = False

        self.preview_menu.entryconfig(0, state=tk.NORMAL if (has_sel or i is not None) else tk.DISABLED)
        self.preview_menu.entryconfig(1, state=tk.NORMAL if has_sel else tk.DISABLED)
        self.preview_menu.entryconfig(2, state=tk.NORMAL if can_paste else tk.DISABLED)
        can_add = i is not None
        can_remove = can_add and self.results[i][0] and not (m and m[0] == 'remove')
        can_clear = can_add and i in self.title_marks
        for idx in (4, 5, 6):
            self.preview_menu.entryconfig(idx, state=tk.NORMAL if can_add else tk.DISABLED)
        self.preview_menu.entryconfig(8, state=tk.NORMAL if can_remove else tk.DISABLED)
        self.preview_menu.entryconfig(9, state=tk.NORMAL if can_clear else tk.DISABLED)
        self.preview_menu.tk_popup(event.x_root, event.y_root)
        return 'break'

    def _on_tree_right_click(self, event):
        row = self.chap_tree.identify_row(event.y)
        if not row:
            return 'break'
        self.chap_tree.selection_set(row); self.chap_tree.focus(row)
        i = self._last_tree_line = self._tree_item_line.get(row)
        m = self.title_marks.get(i) if i is not None else None
        can_remove = i is not None and self.results[i][0] and not (m and m[0] == 'remove')
        self.tree_menu.entryconfig(0, state=tk.NORMAL if i is not None else tk.DISABLED)
        self.tree_menu.entryconfig(2, state=tk.NORMAL if can_remove else tk.DISABLED)
        self.tree_menu.entryconfig(3, state=tk.NORMAL if i is not None and i in self.title_marks else tk.DISABLED)
        self.tree_menu.tk_popup(event.x_root, event.y_root)
        return 'break'

    def jump_to_preview(self):
        i = getattr(self, '_last_tree_line', None)
        if i is None or not self.lines:
            return
        self.right_nb.select(self.frame_preview)  # 先切页：Text 未映射时 see/yview 无效
        self.root.after(50, lambda: self._do_jump_scroll(i))

    def _do_jump_scroll(self, i):
        self.preview.update_idletasks()
        idx = f'{i+1}.0'
        self.preview.see(idx)
        self.preview.yview_scroll(-6, 'units')  # 上移几行留出上下文
        self.preview.tag_remove('sel', '1.0', 'end')
        self.preview.tag_add('sel', idx, f'{i+1}.end')

    # ==================== 缩放（防抖） ====================
    def zoom(self, delta):
        new_p, new_t = self.preview_size + delta, self.tree_size + delta
        in_p, in_t = ZOOM_MIN <= new_p <= ZOOM_MAX, ZOOM_MIN <= new_t <= ZOOM_MAX
        if not (in_p or in_t):
            return
        if in_p:
            self.preview_size = new_p
        if in_t:
            self.tree_size = new_t
        self.zoom_label.config(text=f' 字号: {self.preview_size}*')
        if self._zoom_job is not None:
            self.root.after_cancel(self._zoom_job)
        self._zoom_job = self.root.after(160, self._do_apply_zoom)

    def _do_apply_zoom(self):
        self._zoom_job = None
        self.zoom_label.config(text=f' 字号: {self.preview_size}')
        self._apply_preview_zoom(); self._apply_tree_zoom()

    def _on_ctrl_wheel(self, event):
        self.zoom(1 if event.delta > 0 else -1)
        return 'break'

    # ==================== 判定（含标记覆盖，与 txt2epub 行为对齐） ====================
    def detect(self):
        self.results, self.orig_flags = [], []
        self.rule_stats = {}  # ★ v12：每次重新统计

        # ★ v11：与 txt2epub 保持一致——缩进行开关 / 前导空白字符集合
        ignore_indent = getattr(txt2epub, 'IGNORE_INDENT_LINES', False)
        leading_ws = tuple(getattr(txt2epub, '_LEADING_WS', (' ', '\u3000', '\t', '\u00a0')))

        for i, raw in enumerate(self.lines):
            line_strip = raw.strip()

            # ---- 1) 自动判定（不掺入手动标记，供 orig_flags / 树灰色展示使用）----
            stats = {}
            if ignore_indent and raw[:1] in leading_ws:
                # txt2epub 的行为：缩进行直接当正文，不参与标题判定
                auto_title, auto_level, auto_name = False, -1, None
            else:
                try:
                    # ★ v11：统一走 detect_title(..., stats)——与转换逻辑单一真相源
                    auto_title, auto_level = txt2epub.detect_title(
                        line_strip, i, self.lines, stats)
                    # ★ v12：命中规则在 stats 中 hit>0（被过滤的只记 filtered）
                    # —— 显式判断，避免将来 hit=0 时误判
                    auto_name = None
                    if auto_title:
                        for k, v in stats.items():
                            if v.get('hit', 0) > 0:
                                auto_name = k
                                break
                except TypeError:
                    # 兼容旧版 txt2epub（detect_title 无 stats 参数）
                    auto_title, auto_level = txt2epub.detect_title(line_strip, i, self.lines)
                    auto_name = find_source_rule(line_strip, i, self.lines) if auto_title else None

            # ---- ★ v12：把本行 stats 累计到全局统计（与 txt2epub 打印报告同口径）----
            for k, v in stats.items():
                acc = self.rule_stats.setdefault(k, {'hit': 0, 'filtered': 0})
                acc['hit'] += v.get('hit', 0)
                acc['filtered'] += v.get('filtered', 0)

            self.orig_flags.append((auto_title, auto_level))

            # ---- 2) 手动标记覆盖自动判定 ----
            mark = self.title_marks.get(i)
            if mark:
                kind, lv = mark
                if kind == 'remove':
                    is_title, level, name = False, None, None
                else:
                    is_title, level, name = True, lv, f'手动{LV_NAME[lv]}'
            else:
                is_title, level, name = auto_title, auto_level, auto_name
            if is_title and name is None:
                name = '(来源未识别)'
            self.results.append((is_title, level, name))

        self.color_map = build_color_map(self.results)
        for (lv, name), color in self.color_map.items():
            self.preview.tag_configure(f't{lv}_{name}', foreground=color,
                                       font=(FONT_FAMILY, self.preview_size, 'bold'))

        names = ['全部'] + sorted({r[2] for r in self.results if r[0] and r[2]})
        if any(m[0] == 'add' for m in self.title_marks.values()):
            names.append(FILTER_ADDED)
        if any(m[0] == 'remove' for m in self.title_marks.values()):
            names.append(FILTER_REMOVED)
        self.filter_combo['values'] = names
        if self.filter_var.get() not in names:
            self.filter_var.set('全部')

    # ==================== 渲染：预览与树拆分，互不依赖 ====================
    def render_preview(self):
        """逐行预览：始终全量显示，与筛选无关"""
        show_src = self.show_source_var.get()
        chunks = []
        self._prefix_len = []  # ★ v13：记录前缀长度，供剪贴板坐标换算
        for i, (line, (is_title, level, name)) in enumerate(zip(self.lines, self.results)):
            text = line.rstrip('\r') or ' '
            mark = self.title_marks.get(i)
            if is_title:
                tag = f't{level}_{name}' if (level, name) in self.color_map else 'body'
                marker = '＋' if mark else ''
                label = (f'{marker}[{LV_NAME[level]}·{name}] ' if show_src
                         else f'{marker}[{LV_NAME[level]}] ')
                prefix = f'{i+1:>6} {label}'
                chunks += [(prefix, tag), (text + '\n', tag)]
            else:
                tag = 'body'
                prefix = (f'{i+1:>6} [×] ' if mark and mark[0] == 'remove' and self.orig_flags[i][0]
                          else f'{i+1:>6} ')
                chunks += [(prefix, tag), (text + '\n', tag)]
            self._prefix_len.append(len(prefix))

        self.preview.delete('1.0', 'end')
        buf, cur_tag, n = [], None, 0
        for text, tag in chunks:
            if tag != cur_tag and buf:
                self.preview.insert('end', ''.join(buf), cur_tag); buf, n = [], 0
            cur_tag = tag; buf.append(text); n += 1
            if n > 2000:
                self.preview.insert('end', ''.join(buf), cur_tag); buf, n = [], 0
        if buf:
            self.preview.insert('end', ''.join(buf), cur_tag)

    def render_tree(self):
        """章节树：筛选只在这里生效；计数始终全量"""
        results = self.results
        flt, show_src = self.filter_var.get(), self.show_source_var.get()
        counts = {0: 0, 1: 0, 2: 0}
        for is_t, lv, _n in results:
            if is_t:
                counts[lv] += 1

        def tree_match(i):
            r = results[i]
            if flt == '全部':
                return (r[0], r[1], False)
            if flt == FILTER_ADDED:
                m = self.title_marks.get(i)
                return (bool(m and m[0] == 'add' and r[0]), r[1], False)
            if flt == FILTER_REMOVED:
                # 用原始判定建树，灰色展示
                is_orig, orig_lv = self.orig_flags[i]
                m = self.title_marks.get(i)
                return (bool(m and m[0] == 'remove' and is_orig), orig_lv, True)
            return (r[0] and r[2] == flt, r[1], False)

        # 记录选中 / 展开 / 滚动状态
        sel = self.chap_tree.selection()
        sel_path = self._tree_item_path.get(sel[0]) if sel else None
        expanded, walk = set(), ['']
        while walk:
            p = walk.pop()
            for it in self.chap_tree.get_children(p):
                if self.chap_tree.item(it, 'open'):
                    pt = self._tree_item_path.get(it)
                    if pt:
                        expanded.add(pt)
                walk.append(it)
        yview_frac = self.chap_tree.yview()[0]

        self.chap_tree.delete(*self.chap_tree.get_children())
        for (lv, name), color in self.color_map.items():
            self.chap_tree.tag_configure(f't{lv}_{name}', foreground=color)
        self.chap_tree.tag_configure('removed', foreground='#9e9e9e')

        self._tree_item_path, self._tree_item_line, stack = {}, {}, {}
        insert = self.chap_tree.insert
        for i in range(len(results)):
            show, level, is_removed = tree_match(i)
            if not show:
                continue
            parent_entry = next((stack[k] for k in sorted(stack, reverse=True) if k < level), None)
            parent_id = parent_entry[0] if parent_entry else ''
            parent_path = parent_entry[1] if parent_entry else ()
            title = self.lines[i].strip()
            path = parent_path + ((level, title),)
            mark = self.title_marks.get(i)
            if is_removed:
                prefix, tag = '[×] ', 'removed'
            else:
                name = results[i][2]
                prefix = f'{"＋" if mark else ""}[{name}] ' if show_src else ''
                tag = f't{level}_{name}' if (level, name) in self.color_map else ()
            item_id = insert(parent_id, 'end', text=f'{prefix}{title}', tags=tag,
                             open=(level < 2) or (path in expanded))
            stack[level] = (item_id, path)
            self._tree_item_path[item_id] = path
            self._tree_item_line[item_id] = i
            for k in [k for k in stack if k > level]:
                del stack[k]

        id_by_path = {v: k for k, v in self._tree_item_path.items()}
        if sel_path and sel_path in id_by_path:
            it = id_by_path[sel_path]
            self.chap_tree.selection_set(it); self.chap_tree.focus(it); self.chap_tree.see(it)
        else:
            self.chap_tree.yview_moveto(yview_frac)

        total = sum(counts.values())
        flt_note = f' | 树筛选: {flt}' if flt != '全部' else ''
        mark_note = f' | 标记 {len(self.title_marks)} 处' if self.title_marks else ''
        fp = getattr(self, 'file_path', '')
        # ★ v12：规则明细（与 txt2epub 的控制台报告同口径，格式 规则名(命中/被过滤)）
        if self.rule_stats:
            rule_note = ' | 规则命中: ' + ', '.join(
                f"{n}({s['hit']}/{s['filtered']})" for n, s in self.rule_stats.items()
                if s['hit'] or s['filtered'])
        else:
            rule_note = ''
        self.status_var.set(f'共 {len(self.lines)} 行 | 标题 {total} 个：'
                            f'部 {counts[0]} / 卷 {counts[1]} / 章 {counts[2]}'
                            f'{flt_note}{mark_note}{rule_note}'
                            + (f' | {fp}' if fp else ''))

    def detect_and_render(self):
        if not self.lines:
            return
        self.detect(); self.render_preview(); self.render_tree()

    def open_file(self):
        path = filedialog.askopenfilename(title='选择 txt 文件',
                                          filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')])
        if not path:
            return
        self.file_path = path
        self.lines = read_txt(path).split('\n')
        self.title_marks.clear(); self._mark_undo.clear()  # 换文件清空标记
        self.detect_and_render()

    # ==================== ★ v14：重新读取 txt / 重新读取规则 ====================
    def reload_txt(self):
        """① 重新读取 txt：按当前文件路径从磁盘重读内容并重新判定。
        用于在外部编辑器修改 txt 后快速刷新，无需重新选择文件。"""
        path = getattr(self, 'file_path', '')
        if not path:
            messagebox.showinfo('提示', '尚未打开任何 txt 文件，请先用「打开 txt 文件」选择文件')
            return
        if not os.path.exists(path):
            messagebox.showerror('错误', f'文件不存在或已被移动:\n{path}')
            return
        try:
            self.lines = read_txt(path).split('\n')
        except Exception as e:
            messagebox.showerror('读取失败', f'读取文件失败:\n{e}')
            return
        # 内容重读后行号可能错位，手动标记无法保证对应，清空以保证一致性
        # （如需保留标记，可注释掉下面这一行）
        self.title_marks.clear(); self._mark_undo.clear()
        self.detect_and_render()
        self.status_var.set(f'已重新读取 txt: {path}（共 {len(self.lines)} 行）')

    def reload_rules(self):
        """② 重新读取规则：重载 txt2epub 模块，丢弃界面上未写回源码的临时
        规则修改，恢复为 txt2epub.py 文件中的原始定义。"""
        import importlib
        importlib.reload(txt2epub)
        rebuild_compiled_rules()
        self.refresh_rule_list()
        self.refresh_rules_code()
        # 编辑器表单同步为列表第一条，避免残留已删除规则的旧内容
        children = self.rule_tree.get_children()
        if children:
            self.rule_tree.selection_set(children[0])
            self.on_rule_selected()
        else:
            # 模块里没有任何规则时清空编辑区
            for v in (self.var_name, self.var_level, self.var_mode, self.var_series):
                v.set('')
            self.pattern_text.delete('1.0', 'end')
        if self.lines:
            self.detect_and_render()
        self.status_var.set('已重新读取规则：txt2epub 模块已重载，规则恢复为文件中的原始定义')

    # ==================== EPUB 导出 ====================
    def _find_export_fn(self):
        fn = getattr(txt2epub, EXPORT_FN_NAME, None)
        return (EXPORT_FN_NAME, fn) if callable(fn) else (None, None)

    def export_epub(self):
        if not self.lines:
            messagebox.showwarning('提示', '请先打开 txt 文件'); return
        base = os.path.splitext(os.path.basename(self.file_path))[0] \
            if getattr(self, 'file_path', '') else 'book'
        out = filedialog.asksaveasfilename(title='保存 EPUB', defaultextension='.epub',
                                           initialfile=base + '.epub',
                                           filetypes=[('EPUB 电子书', '*.epub')])
        if not out:
            return
        name, fn = self._find_export_fn()
        if fn is None:
            messagebox.showerror('导出失败',
                                 f'txt2epub 模块中未找到入口函数 {EXPORT_FN_NAME}')
            return

        out_path = os.path.abspath(out)
        # 临时 txt 用原文件主名，保证书名兜底逻辑取到正确书名
        tmp_dir = tempfile.mkdtemp(prefix='txt2epub_')
        tmp_txt = os.path.join(tmp_dir, f'{base}_{str(int(time.time() * 1000))[-6:]}.txt')
        with open(tmp_txt, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.lines))

        marks = self.title_marks
        orig_detect, orig_looks = txt2epub.detect_title, txt2epub.looks_like_title
        orig_ws = tuple(getattr(txt2epub, '_LEADING_WS', ()))
        # ★ NEW：备份 add_first_line_indent，导出期间用补丁版替换，
        # 使"已有前导空白"判断不受 _LEADING_WS 被临时清空的影响
        orig_add_indent = getattr(txt2epub, 'add_first_line_indent', None)
        ignore_indent = getattr(txt2epub, 'IGNORE_INDENT_LINES', False)

        def patched_detect(line_strip, i=0, lines=None, stats=None, *a, **kw):
            # 1) 手动标记最高优先级
            if isinstance(i, int) and marks.get(i):
                if marks[i][0] == 'remove':
                    return (False, -1)  # 与原函数"非标题"返回值一致
                return (True, marks[i][1])
            # 2) ★ v11：还原原版"缩进行直接当正文"的跳过行为
            # （导出期间 _LEADING_WS 被临时清空，缩进行才会走到这里；
            #  这样打在缩进行上的手动"添加"标记也能生效）
            if (ignore_indent and lines is not None and isinstance(i, int)
                    and 0 <= i < len(lines) and lines[i][:1] in orig_ws):
                return (False, -1)
            # 3) 其余走原始判定，stats 照常透传，保持转换后的统计报告可用
            return orig_detect(line_strip, i, lines, stats)

        def patched_looks(text):
            # looks_like_title 不经过 detect_title，也要让标记生效
            stripped = text.strip()
            for i, m in marks.items():
                if i < len(self.lines) and self.lines[i].strip() == stripped:
                    if m[0] == 'add':
                        return True
                    if m[0] == 'remove':
                        return False
            return orig_looks(text)

        def patched_add_indent(line, _ws=orig_ws):
            # ★ NEW：与 txt2epub.add_first_line_indent 逻辑一致，
            # 但"已有前导空白"判断固定使用导出前的真实 _LEADING_WS (_ws)，
            # 不会因为 _LEADING_WS 被临时清空而误判"未缩进"导致双重缩进。
            if not getattr(txt2epub, 'AUTO_FIRST_LINE_INDENT', True):
                return line
            if not line.strip(' \t\u3000\u00a0\r'):
                return line
            if line and line[0] in _ws:
                return line
            return txt2epub._INDENT_STR + line

        txt2epub.detect_title, txt2epub.looks_like_title = patched_detect, patched_looks
        if orig_add_indent is not None:
            txt2epub.add_first_line_indent = patched_add_indent  # ★ NEW
        if ignore_indent:
            txt2epub._LEADING_WS = ()  # 让缩进行也能进入 detect_title，使手动标记生效
        try:
            fn(os.path.dirname(tmp_txt), os.path.basename(tmp_txt))  # 签名 (base_dir, filename)
            produced = os.path.splitext(tmp_txt)[0] + '.epub'
            if not os.path.exists(produced):
                raise RuntimeError('转换完成但未找到产物: ' + produced)
            if os.path.abspath(produced) != out_path:
                if os.path.exists(out_path):
                    os.remove(out_path)
                os.replace(produced, out_path)
            self.status_var.set(f'EPUB 已导出: {out_path}')
            messagebox.showinfo('导出完成', f'EPUB 已导出:\n{out_path}\n\n入口: {name}()，标记已生效')
        except Exception as e:
            messagebox.showerror('导出失败', f'调用 {name} 失败:\n{e}')
        finally:
            txt2epub.detect_title, txt2epub.looks_like_title = orig_detect, orig_looks
            if orig_add_indent is not None:
                txt2epub.add_first_line_indent = orig_add_indent  # ★ NEW
            if ignore_indent:
                txt2epub._LEADING_WS = orig_ws
            try:
                os.unlink(tmp_txt)
            except OSError:
                pass
            try:
                os.rmdir(tmp_dir)  # 残留产物时 rmdir 失败则忽略
            except OSError:
                pass

    # ==================== 规则编辑 ====================
    def refresh_rule_list(self):
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, r in enumerate(txt2epub.TITLE_RULES):
            self.rule_tree.insert('', 'end', iid=str(i),
                                  values=(r['name'], r['level'], r['mode'], r.get('series', '')))

    def on_rule_selected(self, _event=None):
        sel = self.rule_tree.selection()
        if not sel:
            return
        r = txt2epub.TITLE_RULES[int(sel[0])]
        self.var_name.set(r['name']); self.var_level.set(str(r['level']))
        self.var_mode.set(r['mode']); self.var_series.set(r.get('series', ''))
        self.pattern_text.delete('1.0', 'end')
        self.pattern_text.insert('1.0', r['pattern'])

    def _collect_editor(self):
        name = self.var_name.get().strip()
        pattern = self.pattern_text.get('1.0', 'end').rstrip('\n')
        if not name or not pattern.strip():
            messagebox.showwarning('提示', '规则名和正则不能为空'); return None
        try:
            level = int(self.var_level.get()); assert level in (0, 1, 2)
        except Exception:
            messagebox.showwarning('提示', '层级必须是 0 / 1 / 2'); return None
        mode = self.var_mode.get().strip()
        if mode not in ('match', 'search'):
            messagebox.showwarning('提示', "mode 只能是 'match' 或 'search'"); return None
        series = self.var_series.get().strip() or None
        try:
            re.compile(pattern, re.VERBOSE)
        except re.error as e:
            hint = '\n提示：f-string 量词如 {3,4} 需写成 {{3,4}}' if '{' in pattern else ''
            messagebox.showerror('正则错误', f'{e}{hint}'); return None
        rule = dict(name=name, level=level, mode=mode, pattern=pattern)
        if series:
            rule['series'] = series
        return rule

    def save_rule(self):
        sel = self.rule_tree.selection()
        if not sel:
            messagebox.showinfo('提示', '请先在列表中选中一条规则'); return
        rule = self._collect_editor()
        if rule:
            txt2epub.TITLE_RULES[int(sel[0])] = rule
            self.refresh_rule_list(); self.refresh_rules_code(); self.apply_rules()

    def add_rule(self):
        rule = self._collect_editor()
        if rule:
            if any(r['name'] == rule['name'] for r in txt2epub.TITLE_RULES):
                messagebox.showwarning('提示', f"规则名 '{rule['name']}' 已存在"); return
            txt2epub.TITLE_RULES.insert(0, rule)
            self.refresh_rule_list(); self.refresh_rules_code(); self.apply_rules()

    def del_rule(self):
        sel = self.rule_tree.selection()
        if sel:
            del txt2epub.TITLE_RULES[int(sel[0])]
            self.refresh_rule_list(); self.refresh_rules_code(); self.apply_rules()

    def move_rule(self, delta):
        sel = self.rule_tree.selection()
        if not sel:
            return
        i, j, rules = int(sel[0]), int(sel[0]) + delta, txt2epub.TITLE_RULES
        if 0 <= j < len(rules):
            rules[i], rules[j] = rules[j], rules[i]
            self.refresh_rule_list(); self.rule_tree.selection_set(str(j))
            self.refresh_rules_code(); self.apply_rules()

    def apply_rules(self):
        try:
            rebuild_compiled_rules()
        except re.error as e:
            messagebox.showerror('编译失败', str(e)); return
        self.refresh_rules_code()
        if self.lines:
            self.detect_and_render()

    def refresh_rules_code(self):
        out = ['TITLE_RULES = [']
        for r in txt2epub.TITLE_RULES:
            # ★ v11：带上 series 字段，复制回 txt2epub.py 不再丢失目录页过滤配置
            series = f", series={r['series']!r}" if r.get('series') else ''
            out.append(f"    dict(name={r['name']!r}, level={r['level']}, mode={r['mode']!r}{series},")
            out.append(f"          pattern={r['pattern']!r}),")
        out.append(']')
        self.code_text.delete('1.0', 'end')
        self.code_text.insert('1.0', '\n'.join(out))


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    root = tk.Tk()
    PreviewApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
