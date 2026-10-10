import os
import re
import zipfile
import uuid
#from xml.etree import ElementTree as ET

# preview_titles.py
# …… 「」
# 功能开关：True 时，正文中没有首行缩进的段落自动补 2 个全角空格
AUTO_FIRST_LINE_INDENT = True
# 缩进宽度：全角空格（U+3000）个数，中文排版规范为 2 字符
INDENT_WIDTH = 2

# 功能开关：True 时，行首带缩进（全角/半角空格、Tab、不间断空格）的行
# 直接视为正文，不参与标题判定（也就不会被识别为章节标题）
IGNORE_INDENT_LINES = 1

# ★ NEW 功能开关：True 时，每章标题下方插入一行字数统计标签
SHOW_CHAPTER_WORD_COUNT = True

# ★ NEW 标签格式：{count} 会被替换成字数；可自行改成
# '本章约 {count} 字' / '（{count} 字）' 等
WORD_COUNT_FORMAT = '本章共 {count} 字'

# ★ NEW 字数统计口径开关：True 时剔除所有空白字符（空格/全角空格/Tab/换行）再计数
# False 时直接 len()，会把缩进空格也算进去
WORD_COUNT_STRIP_WS = True

# ★ NEW 功能开关：True 时，若书中存在卷级标题（level 1），
# 在目录页开头额外生成一排"卷索引"链接，点击可跳到目录页内各卷的位置。
SHOW_TOC_VOLUME_INDEX = True

# ============================================================
# 标题识别配置区 —— ★ 以后新增/删除/调优先级，只改这个列表
#
# 每条规则字段：
#   name  : 规则名（同时作为连续列举过滤 _SERIES_RULES 的 key）
#   level : 层级 0=部/篇 1=卷 2=章
#   mode  : 'search' 行内任意位置匹配 / 'match' 必须行首匹配
#   series: 可选，需做"目录页连续列举"过滤时填 key，否则省略
#
# ★ 优先级 = 列表顺序，靠前的先判定（想改优先级直接调换顺序）
# ★ 正则里 {n} 量词要写成 {{n}}（因为下面用 f-string 拼接）
# ============================================================

# 最大标题长度：超过此长度的行不视作标题
MAX_TITLE_LEN = 50

# 数字片段：阿拉伯数字 / 简体中文数字 / 繁体大写数字，1~6 位
_NUM = r'[\d零一二三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟]{1,6}'
_NUM13 = r'[\d零一二三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟]{1,3}'
_NUM_DOT = r'[\d零一二三四五六七八九十序]+'

# 分隔符片段：空白 / 半全角冒号 / 各类括号 / 破折号 / 下划线 / 装饰符 / 圆圈数字
_SEP = r'[\s：:（)（）【】《》\[\]\-—~＿_★·\/#①②③④⑤⑥⑦⑧⑨⑩— ]'

# 关键词章名集合：序章 / 尾声 / 楔子 / 外传 / 番外 / 终章 …… 等无序号章节名
_KEYS = (
    r'('
    r'序章|序|序幕|尾声|楔子|外传|前传|前言|简介|附录|作品相关|上架感言|上架感言及悬赏|作者的话|完本感言|完结感言|'
    r'后记|特典|序言|绪论|番外|番外篇|大结局|结局|日后谈|后日谈|间章|写在最后|幕间|'
    r'作品相关|最终章|终章|终幕'
    r')'
)

# ============================================================
# ★★ 正则规则表：所有标题规则集中在此 ★★
# ============================================================
#
# search：行内任意位置匹配；match：必须行首匹配【match ≈ 模式隐式加了^。对所有的分支都会加:^分支1 | ^分支2 | ^分支3】
# VERBOSE 的副作用：它会把正则里的空格忽略掉， 要匹配字面空格时必须写成 \ （转义空格）
TITLE_RULES = [
    # ---- level 2：章/节类 ----

    # 「第N章」/「第N节」/「章N」（不带「第」）
    dict(name='chap_num', level=2, mode='search',
         pattern=rf'''
         第{_NUM}[章节](?={_SEP}|$)
         |
         章{_NUM}(?={_SEP}|$)
         '''),

    # 关键词章名：行首为序章 / 尾声 / 楔子 / 番外…… 等关键词，后接分隔符或行尾
    dict(name='key', level=2, mode='match',
         pattern=rf'^(?:{_SEP}{{0,1}})?{_KEYS}(?={_SEP}|$)'),

    # 序号点号：行首数字（含中文数字）后接半角/全角点号或右括号：1. / 2、 / 3）等
    # series='dot'：做目录页连续列举过滤
    #dict(name='dot', level=2, mode='match', series='dot',
    #     pattern=r'^\s*[\d零一二三四五六七八九十序]+[\.\~\、\ \）\)](?!\.)'),

    # ── dot 规则：两个分支 ──
    # 分支1：普通序号 1. / 2、 / 3） / 10.基本操作（点号后不能是数字或另一个点）
    # 分支2：卷章式/数字章名 2.2 / 3.15 / 12.1943年的雪
    #        首位必须1~9（排除0.2小数），且整行不得以句号结尾（排除散文正文）
    dict(name='dot', level=2, mode='match', series='dot',
         pattern=rf'^\s*{_NUM_DOT}[\.\~\、\ \）\)](?![\.\d])|^\s*[1-9]\d*\.\d+(?!.*。$)'),

    # No 序号：行首 No、 后接 3~4 位数字，数字后须跟分隔符或行尾
    # series='no'：做目录页连续列举过滤
    dict(name='no', level=2, mode='match', series='no',
         pattern=rf'^\s*No、\s*\d{{3,4}}(?={_SEP}|$)'),

    # ---- level 1：卷 ----
    # r = raw string，防止 \s、\d 被 Python 转义
    # f = format string，{变量名} 会被替换成变量的值，
    #     受此影响，正则里原本写{1,4}这种的需要写成{{1,4}}，即{aaa}变{{aaa}}
    dict(name='volx', level=1, mode='match',
         pattern=rf'^{_SEP}{{0,2}}卷{_NUM13}(?={_SEP}|$) '
         ),

    dict(name='vol', level=1, mode='search',
         pattern=rf'''\[卷名\].*                          # [卷名] 显式标记开头，整行作卷名
         | 卷名[\s：:]+.*                         # 「卷名」+ 分隔符 + 文字
         | ^{_SEP}卷{_NUM}(?={_SEP}|$)                # 「卷N」（如「卷一」）。卷 前面必须是【行首 和 分隔符】
         | ^卷{_NUM}(?:完结|总结)。$                  # 「卷N完结。」
         | (?:^|{_SEP})第{_NUM}部第{_NUM}卷(?={_SEP}|$)
         | (?:^|{_SEP})第{_NUM}卷(?={_SEP}|$)         # 「第N卷」
         | (?:^|{_SEP})最终卷(?={_SEP}|$)             # 「最终卷」
         | 📖.{{4,12}}📖
         | ^{_SEP}?[^\s{_SEP}]卷{_NUM}(?={_SEP}|$)    # 带单字符前缀的「卷N」（如「正卷一」「续卷一」）
         '''),

    # ---- level 0：部/篇（顶层）----
    # 匹配「第N部」「第N篇」「最终部」，其后须紧跟分隔符或行尾
    dict(name='bu', level=0, mode='search',
         pattern=rf'''第{_NUM}[部篇](?={_SEP}|$)
         | 最终部(?={_SEP}|$)'''),
]

# ---- 统一编译，只编译一次 ----
# COMPILED_RULES 元素：(规则名, 层级, 匹配方式, 列举过滤key或None, 编译后的正则)
COMPILED_RULES = [
    (r['name'], r['level'], r['mode'], r.get('series'), re.compile(r['pattern'], re.VERBOSE))
    for r in TITLE_RULES
]

# ============================================================
# 连续列举过滤规则表（目录页保护）：key -> (line_re, item_re)
#   line_re : 判定某行是否属于该类序号行（用于相邻行检查）
#   item_re : 统计同一行内该类序号出现次数（>1 视为目录列举行）
# ★ key 与上面规则表的 series 字段对应；新增规则若需要过滤，在这里加一项即可
# ============================================================
_SERIES_RULES = {
    'dot': (
        # line_re：判定某行是否为「数字+点号/顿号」开头的序号行
        re.compile(r'^\s*[\d零一二三四五六七八九十序]+[\.\、\ ]'),
        # item_re：统计同一行内该类序号出现次数
        re.compile(r'[\d序零一二三四五六七八九十]+[\.\、\ ]'),
    ),
    'no': (
        # line_re：直接复用 No 序号形态
        re.compile(r'^\s*No、\s*\d{3,4}(?=' + _SEP + r'|$)'),
        # item_re：No、NNN 形态
        re.compile(r'No、\s*\d{3,4}'),
    ),
}


def is_series_listing(i, lines, key):
    """
    判断第 i 行是否属于目录页连续列举（应取消其标题判定）。
    两种触发条件满足其一即为真：
      1. 同一行内出现多个同类序号（如「1. xxx 2. xxx」）
      2. 相邻行（上一行或下一行）也是同类序号行
    """
    line_re, item_re = _SERIES_RULES[key]
    line = lines[i].strip()

    # 条件 1：同行多个序号
    if len(item_re.findall(line)) > 1:
        return True

    # 条件 2：相邻行同类
    prev_line = lines[i - 1].strip() if i > 0 else ''
    next_line = lines[i + 1].strip() if i < len(lines) - 1 else ''
    return bool(line_re.match(prev_line) or line_re.match(next_line))


def detect_title(line_strip, i, lines, stats=None):
    """
    ★ 统一标题判定入口（判定逻辑只写这一次）。
    按 TITLE_RULES 的列表顺序（即优先级）依次尝试。
    返回 (is_title, level)；不是标题返回 (False, -1)。

    ★ NEW：stats —— 可选字典，用于统计每条规则的命中/被过滤次数，形如：
        { 'chap_num': {'hit': 120, 'filtered': 3}, ... }
      传入 None（默认）则不统计，其他调用点（如 looks_like_title）完全不受影响。
    """
    # 仅对长度不超过阈值的行做标题检测
    if not line_strip or len(line_strip) > MAX_TITLE_LEN:
        return False, -1

    for name, level, mode, series_key, rx in COMPILED_RULES:
        # search：行内任意位置匹配；match：必须行首匹配
        hit = rx.search(line_strip) if mode == 'search' else rx.match(line_strip)
        if not hit:
            continue

        # 连续列举过滤（目录页保护）：被过滤则继续尝试下一条规则
        if series_key and is_series_listing(i, lines, series_key):
            # ★ NEW：记录被过滤次数
            if stats is not None:
                s = stats.setdefault(name, {'hit': 0, 'filtered': 0})
                s['filtered'] += 1
            continue

        # ★ NEW：命中，按规则名计数
        if stats is not None:
            s = stats.setdefault(name, {'hit': 0, 'filtered': 0})
            s['hit'] += 1
        return True, level

    return False, -1


def looks_like_title(text):
    """
    简化版：只判断一段文本是否像标题（用于首行书名识别），不做列举过滤。
    """
    if not text or len(text) > MAX_TITLE_LEN:
        return False
    for name, level, mode, series_key, rx in COMPILED_RULES:
        if (rx.search(text) if mode == 'search' else rx.match(text)):
            return True
    return False


# 缩进字符串：全角空格 ×INDENT_WIDTH
_INDENT_STR = '\u3000' * INDENT_WIDTH

# 视为"已有前导空白"的字符集合：半角空格 / 全角空格 / Tab / 不间断空格
_LEADING_WS = (' ', '\u3000', '\t', '\u00a0')


def add_first_line_indent(line):
    """
    为正文段落行添加首行缩进。
    - 空行 / 纯空白行：原样返回（不加缩进）
    - 行首已有任意前导空白（含全角/半角空格、Tab）：视为已缩进，原样保留
    - 其余行：行首补全角空格 ×INDENT_WIDTH
    """
    if not AUTO_FIRST_LINE_INDENT:
        return line
    # 空行判断：显式传入空白字符集合（含全角空格），
    # 不依赖 strip() 默认行为，避免不同环境下对 U+3000 处理不一致
    if not line.strip(' \t\u3000\u00a0\r'):
        return line
    # 已有前导空白则不动，防止重复叠加缩进
    if line and line[0] in _LEADING_WS:
        return line
    return _INDENT_STR + line


def count_words(chapter_lines):
    """
    ★ NEW：统计一章的字数。
    - WORD_COUNT_STRIP_WS=True 时剔除空白字符（半角/全角空格、Tab、不间断空格、\\r、\\n）
    - 统计的是该章所有正文行的总字符数（含中英文、标点、数字）
    """
    text = ''.join(chapter_lines)
    if WORD_COUNT_STRIP_WS:
        text = re.sub(r'[ \t\u3000\u00a0\r\n]', '', text)
    return len(text)


def main():
    current_dir = os.getcwd()
    txt_files = [f for f in os.listdir(current_dir) if f.lower().endswith('.txt')]
    if not txt_files:
        print("未发现txt文件。")
        return

    print(f"开始处理 (恢复可视目录页)，共 {len(txt_files)} 个文件...")
    for txt_file in txt_files:
        try:
            process_file_native_with_toc(current_dir, txt_file)
        except Exception as e:
            print(f"处理文件 {txt_file} 出错: {e}")
            import traceback
            traceback.print_exc()


def process_file_native_with_toc(base_dir, filename):
    txt_path = os.path.join(base_dir, filename)
    safe_filename = os.path.splitext(filename)[0]
    epub_name = safe_filename + '.epub'
    epub_path = os.path.join(base_dir, epub_name)

    print(f"\n正在转换: {filename}")

    # 1. 读取文件
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(txt_path, 'r', encoding='gbk') as f:
            content = f.read()

    lines = content.split('\n')

    # 2. 智能提取书名
    book_title = ""
    start_index = 0
    first_line = lines[0].strip() if lines else ""
    is_title_line = False

    # 判定首行是否本身就是一个章节标题（若是，则书名改取自文件名）
    if first_line and len(first_line) < 20:
        # ★ 统一走 looks_like_title()，不再逐条罗列正则
        is_title_line = looks_like_title(first_line)

    if first_line and not is_title_line:
        book_title = first_line
        start_index = 1
    else:
        book_title = safe_filename
        start_index = 0

    if not book_title or not book_title.strip():
        book_title = safe_filename

    # 3. 内容收集
    chapters_data = []
    chapters_data.append({'title': '扉页', 'lines': [], 'level': 2})

    current_chunk = []
    is_title_num = 0      # 目录标题数统计
    rule_stats = {}       # ★ NEW：各规则命中/被过滤统计

    for i in range(start_index, len(lines)):
        line = lines[i]
        line_strip = line.strip()

        # ★ 新增开关：缩进行（如以「　」开头）直接当正文处理，不做标题判定
        #if IGNORE_INDENT_LINES and line.startswith('\u3000\u3000'):
        if IGNORE_INDENT_LINES and line[:1] in _LEADING_WS:
            current_chunk.append(line)
            continue

        # ★ 统一判定：一行调用搞定全部规则的匹配 + 优先级 + 列举过滤
        # ★ NEW：传入 rule_stats 收集统计
        is_title, level = detect_title(line_strip, i, lines, rule_stats)
        if is_title:
            if current_chunk:
                chapters_data[-1]['lines'] = current_chunk
            current_chunk = [line]
            chapters_data.append({'title': line_strip, 'lines': [], 'level': level})
            is_title_num += 1
        else:
            current_chunk.append(line)

    if current_chunk:
        chapters_data[-1]['lines'] = current_chunk

    if len(chapters_data) > 1 and not chapters_data[0]['lines']:
        chapters_data.pop(0)

    # 4. 原生 ZIP 打包 EPUB (含可视目录页)
    def escape_html(text):
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 预先分配 ID 和 href
    for idx, chap in enumerate(chapters_data):
        chap['id'] = f'chap_{idx}'
        chap['href'] = f'chap_{idx}.xhtml'

    try:
        if os.path.exists(epub_path):
            os.remove(epub_path)

        with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 1. mimetype
            zf.writestr('mimetype', 'application/epub+zip',
                        compress_type=zipfile.ZIP_STORED)

            # 2. container.xml
            zf.writestr('META-INF/container.xml', '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')

            # 3. 准备 OPF 和 NCX 数据结构
            manifest_xml = []
            spine_xml = []

            # --- 写入 扉页 ---
            zf.writestr('OEBPS/title.xhtml',
                        f'<html><body><h2 style="text-align:center">{escape_html(book_title)}</h2></body></html>')
            manifest_xml.append('<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>')
            spine_xml.append('<itemref idref="title"/>')

            # ★ NEW：预扫描全书，收集所有卷级标题，用于生成目录页开头的"卷索引"
            # vol_entries 元素：(卷序号, 转义后的卷名)
            # 卷序号与下方主循环中 vol_num 的递增顺序严格一致，
            # 因此锚点 id "toc-vol-{N}" 能与索引链接一一对应
            vol_entries = []
            _vol_cnt = 0
            if SHOW_TOC_VOLUME_INDEX:
                for chap in chapters_data:
                    if chap['level'] == 1:
                        _vol_cnt += 1
                        t = re.sub(r'^\s*\d+\s*[\.、．]\s*', '', chap['title'])
                        vol_entries.append((_vol_cnt, escape_html(t)))

            # --- 写入 目录页 ---
            toc_html_lines = [
                #list-style: none; /* 去掉编号 */
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">',
                '<head><style>',
                'body { padding: 10px; } h2 { text-align: center; }',
                'nav ol {margin: 0; padding-left: 0; }',
                'nav ol ol { padding-left: 1.5em; }',
                'nav li { font-size: 0.9em; margin: 2px 0; text-align: left; }',
                '</style></head><body>',
                '<nav epub:type="toc" id="toc" role="doc-toc">',
                '<h2>目录</h2>',
                '<ol>',  # ★ 外层列表，先开启
            ]

            vol_num = 0    # 卷号
            chap_num = 0   # 卷内章号（每换一卷归零）
            loose_num = 0  # ★ 不属于任何卷的章，独立编号
            sub_open = False  # 卷内子 <ol> 是否开启
            top_open = False  # ★ 是否正处在某个卷的 <li> 内

            for chap in chapters_data:
                href = escape_html(chap['href'])
                title = re.sub(r'^\s*\d+\s*[\.、．]\s*', '', chap['title'])
                title = escape_html(title)

                if chap['level'] == 1:
                    # ---- 卷：收拾残局，开新卷 ----
                    if sub_open:
                        toc_html_lines.append('</ol>')
                        sub_open = False
                    if top_open:
                        toc_html_lines.append('</li>')
                    vol_num += 1
                    chap_num = 0
                    # ★ NEW：加锚点 id，供目录页开头的卷索引跳转
                    toc_html_lines.append(
                        f'<li class="toc-vol" id="toc-vol-{vol_num}"><a href="{href}"> {title}</a>')
                    top_open = True
                elif chap['level'] == 2:
                    if top_open:
                        # ---- 章在卷内：挂子 ol ----
                        if not sub_open:
                            toc_html_lines.append('<ol>')
                            sub_open = True
                        chap_num += 1
                        toc_html_lines.append(f'<li><a href="{href}"> {title}</a></li>')
                    else:
                        # ---- ★ 章不在任何卷内：作为外层直接条目 ----
                        loose_num += 1
                        toc_html_lines.append(f'<li><a href="{href}"> {title}</a></li>')
                else:
                    # ---- level 0（如"简介"）：不编号的顶级条目 ----
                    if sub_open:
                        toc_html_lines.append('</ol>')
                        sub_open = False
                    if top_open:
                        toc_html_lines.append('</li>')
                        top_open = False
                    toc_html_lines.append(f'<li><a href="{href}">{title}</a></li>')

            # ---- 收尾 ----
            if sub_open:
                toc_html_lines.append('</ol>')
            if top_open:
                toc_html_lines.append('</li>')
            toc_html_lines.append('</ol>')  # 外层列表
            toc_html_lines.append('</nav>')
            toc_html_lines.append('</body></html>')

            # ★ NEW：生成"卷索引"块并插入到 <h2>目录</h2> 之后
            # 样式与章首字数统计标签一致：color:gray; font-size:0.7em
            if SHOW_TOC_VOLUME_INDEX and vol_entries:
                links = ''.join(
                    f'<a href="#toc-vol-{num}" '
                    f'style="display:block; color:gray; font-size:0.8em; '
                    f'text-decoration:none; margin:1px 0; text-align:left">{t}</a>'
                    for num, t in vol_entries
                )
                vol_index_block = (
                    '<div style="margin:0.2em 0 1em 0">'
                    f'{links}'
                    '</div>'
                )
                # 插到 '<h2>目录</h2>' 之后、'<ol>' 之前
                h2_idx = toc_html_lines.index('<h2>目录</h2>')
                toc_html_lines.insert(h2_idx + 1, vol_index_block)

            zf.writestr('OEBPS/contents.xhtml', "".join(toc_html_lines))
            manifest_xml.append('<item id="contents" href="contents.xhtml" media-type="application/xhtml+xml"/>')
            spine_xml.append('<itemref idref="contents"/>')

            # --- NCX 层级逻辑构建 ---
            ncx_nav_map = []
            nav_parents = {-1: 'navpoint_root'}
            ncx_nav_map.insert(0, {
                'id': 'nav_contents',
                'parent': 'navpoint_root',
                'title': '目录',
                'href': 'contents.xhtml'
            })

            # --- 写入正文章节 ---
            for chap in chapters_data:
                uid = chap['id']
                href = chap['href']

                html_body = []
                html_body.append(f'<html><head><title>{escape_html(chap["title"])}</title></head><body>')
                html_body.append(f'<h2>{escape_html(chap["title"])}</h2>')

                if chap['lines']:
                    start_idx = 0
                    if chap['lines'][0].strip() == chap['title']:
                        start_idx = 1

                    # ★ NEW：字数统计标签（可开关），统计的是去掉标题行之后的正文
                    if SHOW_CHAPTER_WORD_COUNT:
                        wc = count_words(chap['lines'][start_idx:])
                        html_body.append(
                            f'<p style="text-align:left; color:gray; font-size:0.7em; '
                            f'margin:0.2em 0 1em 0">{escape_html(WORD_COUNT_FORMAT.format(count=wc))}</p>'
                        )

                    for l in chap['lines'][start_idx:]:
                        #html_body.append(f'<p>{escape_html(l)}</p>')
                        html_body.append(f'<p>{escape_html(add_first_line_indent(l))}</p>')

                html_body.append('</body></html>')
                zf.writestr(f'OEBPS/{href}', "".join(html_body))

                manifest_xml.append(f'<item id="{uid}" href="{href}" media-type="application/xhtml+xml"/>')
                spine_xml.append(f'<itemref idref="{uid}"/>')

                current_level = chap['level']
                my_id = f'nav_{uid}'
                parent_level = current_level - 1
                while parent_level not in nav_parents and parent_level >= -1:
                    parent_level -= 1
                if parent_level < -1:
                    parent_level = -1
                parent_id = nav_parents[parent_level]
                nav_parents[current_level] = my_id
                to_remove = [k for k in nav_parents if k > current_level]
                for k in to_remove:
                    del nav_parents[k]

                ncx_nav_map.append({
                    'id': my_id,
                    'parent': parent_id,
                    'title': chap['title'],
                    'href': href
                })

            # --- 写入 OPF ---
            unique_id = str(uuid.uuid4())
            opf_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{escape_html(book_title)}</dc:title>
    <dc:language>zh-CN</dc:language>
    <dc:identifier id="uid">urn:uuid:{unique_id}</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    {"".join(manifest_xml)}
  </manifest>
  <spine toc="ncx">
    {"".join(spine_xml)}
  </spine>
</package>'''
            zf.writestr('OEBPS/content.opf', opf_content)

            def build_nav_map(nodes, parent_id, counter=None, depth=1):
                """
                递归构建 NCX navMap 内容，同时动态计算 playOrder 与最大深度。

                参数:
                    nodes     : ncx_nav_map 列表，每项含 id/parent/title/href
                    parent_id : 当前父节点 ID ('navpoint_root' 为根)
                    counter   : list 容器, counter[0] 为当前已分配的最大 playOrder
                                外部初始传入 [0] 即可；递归内部自动沿用
                    depth     : 当前递归深度（根节点的直接子节点为 1）

                返回:
                    (xml_str, max_depth) —— 子树 XML 字符串 与 本子树的最大层级数
                """
                if counter is None:
                    counter = [0]
                xml_str = ""
                children = [n for n in nodes if n['parent'] == parent_id]
                max_depth = 0 if not children else depth
                for child in children:
                    counter[0] += 1  # ✅ playOrder 全书连续递增
                    sub_xml, sub_depth = build_nav_map(nodes, child['id'], counter, depth + 1)
                    max_depth = max(max_depth, sub_depth)  # ✅ 记录真实嵌套最深值
                    xml_str += f'''
    <navPoint id="{child['id']}" playOrder="{counter[0]}">
      <navLabel><text>{escape_html(child['title'])}</text></navLabel>
      <content src="{child['href']}"/>
      {sub_xml}
    </navPoint>'''
                return xml_str, max_depth

            # --- 构建 navMap 并取回真实深度 ---
            counter = [0]  # playOrder 从 1 开始
            nav_map_content, real_depth = build_nav_map(
                ncx_nav_map, 'navpoint_root', counter, depth=1
            )
            real_depth = max(real_depth, 1)

            # --- 写入 NCX ---
            ncx_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{unique_id}"/>
    <meta name="dtb:depth" content="{real_depth}"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{escape_html(book_title)}</text></docTitle>
  <navMap>
    {nav_map_content}
  </navMap>
</ncx>'''
            zf.writestr('OEBPS/toc.ncx', ncx_content)

        print(f"    -> 完成: {epub_name}")
        print(f"       统计：发现{is_title_num}个标题")

        # ★ NEW：各规则识别明细报告
        total_hit = sum(s['hit'] for s in rule_stats.values())
        total_filtered = sum(s['filtered'] for s in rule_stats.values())
        print("       各规则识别明细（命中 / 被目录列举过滤）：")
        for r in TITLE_RULES:  # 按 TITLE_RULES 声明顺序输出
            name = r['name']
            s = rule_stats.get(name, {'hit': 0, 'filtered': 0})
            print(f"         {name:<10} 命中 {s['hit']:>5} 个 | 过滤 {s['filtered']:>4} 次")
        print(f"         {'合计':<10} 命中 {total_hit:>5} 个 | 过滤 {total_filtered:>4} 次")

        print(f"       结构: 扉页 -> 目录页 -> 正文")

    except Exception as e:
        print(f"写入EPUB出错: {e}")


if __name__ == '__main__':
    main()
