# -*- coding: utf-8 -*-
"""Generate a PPT about China's Rural Revitalization Strategy."""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn

try:
    from pptx.oxml import OxmlElement
except ImportError:
    OxmlElement = None

FONT = "Microsoft YaHei"
GREEN = RGBColor(0x1B, 0x5E, 0x20)
GREEN2 = RGBColor(0x2E, 0x7D, 0x32)
LIGHT_GREEN = RGBColor(0xE8, 0xF5, 0xE9)
BG = RGBColor(0xF7, 0xFB, 0xF7)
GOLD = RGBColor(0xD9, 0xA5, 0x3B)
DARK = RGBColor(0x2B, 0x2B, 0x2B)
GRAY = RGBColor(0x6B, 0x7B, 0x6D)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BORDER = RGBColor(0xD3, 0xE6, 0xD5)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def style_run(run, size, color, bold=False, font=FONT):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font
    if OxmlElement is not None:
        try:
            rPr = run._r.get_or_add_rPr()
            ea = rPr.find(qn('a:ea'))
            if ea is None:
                ea = OxmlElement('a:ea')
                rPr.append(ea)
            ea.set('typeface', font)
        except Exception:
            pass


def add_rect(slide, x, y, w, h, color, rounded=False):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    shp.line.fill.background()
    return shp


def add_text(slide, x, y, w, h, text, size, color, bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, line_spacing=1.15):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = line_spacing
    run = p.add_run()
    run.text = text
    style_run(run, size, color, bold)
    return box


def add_bullets(slide, x, y, w, h, items, size=15, color=DARK, gap=10):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.line_spacing = 1.2
        run = p.add_run()
        run.text = "•  " + item
        style_run(run, size, color)
    return box


def add_card(slide, x, y, w, h, title, lines=None, num=None, fill=WHITE,
             title_size=18, body_size=13):
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                  Inches(x), Inches(y), Inches(w), Inches(h))
    card.fill.solid()
    card.fill.fore_color.rgb = fill
    card.line.color.rgb = BORDER
    card.line.width = Pt(1)
    add_rect(slide, x, y, w, 0.07, GOLD)
    ty = y + 0.28
    if num:
        add_text(slide, x + 0.28, ty, 0.7, 0.4, num, 20, GOLD, True)
        tx = x + 1.0
        tw = w - 1.3
    else:
        tx = x + 0.28
        tw = w - 0.56
    add_text(slide, tx, ty, tw, 0.5, title, title_size, GREEN, True)
    if lines:
        add_bullets(slide, x + 0.28, ty + 0.62, w - 0.56, h - 1.0,
                    lines, size=body_size, gap=8)


def content_slide(title, page, subtitle=None):
    slide = prs.slides.add_slide(BLANK)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = BG
    add_rect(slide, 0, 0, 13.333, 1.15, GREEN)
    add_rect(slide, 0, 1.15, 13.333, 0.06, GOLD)
    add_text(slide, 0.65, 0.28, 10.5, 0.65, title, 28, WHITE, True)
    if subtitle:
        add_text(slide, 9.6, 0.42, 3.1, 0.4, subtitle, 12,
                 RGBColor(0xC8, 0xE6, 0xC9), align=PP_ALIGN.RIGHT)
    add_text(slide, 0.65, 7.02, 6.0, 0.3, "乡村振兴 · 专题汇报", 10, GRAY)
    add_text(slide, 11.9, 7.02, 0.8, 0.3, "第 %d 页" % page, 10, GRAY,
             align=PP_ALIGN.RIGHT)
    return slide


# ---------- Slide 1: Cover ----------
s = prs.slides.add_slide(BLANK)
s.background.fill.solid()
s.background.fill.fore_color.rgb = GREEN
add_rect(s, 0, 0, 13.333, 0.12, GOLD)
add_rect(s, 0.9, 2.05, 0.12, 1.9, GOLD)
add_text(s, 1.35, 1.95, 10.6, 1.2, "乡村振兴", 60, WHITE, True)
add_text(s, 1.4, 3.25, 10.6, 0.6,
         "全面推进乡村全面振兴 · 建设宜居宜业和美乡村", 22,
         RGBColor(0xC8, 0xE6, 0xC9))
add_rect(s, 1.4, 4.15, 4.2, 0.03, GOLD)
add_text(s, 1.4, 4.5, 10.6, 0.5,
         "产业兴旺 · 生态宜居 · 乡风文明 · 治理有效 · 生活富裕", 16,
         RGBColor(0xA5, 0xD6, 0xA7))
add_text(s, 1.4, 6.55, 10.6, 0.4, "政策学习专题汇报", 14, WHITE)

# ---------- Slide 2: Contents ----------
s = content_slide("目录  /  CONTENTS", 2)
items = [
    ("01", "战略背景", "战略提出与重大意义"),
    ("02", "总要求：二十字方针", "产业、生态、乡风、治理、生活"),
    ("03", "五大振兴", "产业·人才·文化·生态·组织"),
    ("04", "三步走目标", "2020 / 2035 / 2050"),
    ("05", "重点任务与实施路径", "底线任务与关键举措"),
    ("06", "结语", "农业强 · 农村美 · 农民富"),
]
for i, (num, title, desc) in enumerate(items):
    col, row = i % 2, i // 2
    x, y = 0.65 + col * 6.25, 1.6 + row * 1.7
    add_rect(s, x, y, 5.85, 1.4, WHITE, rounded=True)
    add_rect(s, x, y, 0.1, 1.4, GREEN2)
    add_text(s, x + 0.35, y + 0.28, 1.0, 0.7, num, 28, GOLD, True)
    add_text(s, x + 1.45, y + 0.22, 4.1, 0.5, title, 19, GREEN, True)
    add_text(s, x + 1.45, y + 0.78, 4.1, 0.4, desc, 12, GRAY)

# ---------- Slide 3: Background ----------
s = content_slide("一、战略背景与重大意义", 3, "BACKGROUND")
add_card(s, 0.65, 1.5, 5.85, 2.35, "战略提出", [
    "2017年10月，党的十九大报告首次提出实施乡村振兴战略",
    "2017年12月中央农村工作会议对实施乡村振兴战略作出系统部署",
])
add_card(s, 6.85, 1.5, 5.85, 2.35, "战略地位", [
    "坚持把解决好“三农”问题作为全党工作重中之重",
    "总方针：坚持农业农村优先发展",
])
add_card(s, 0.65, 4.05, 5.85, 2.35, "总要求与总目标", [
    "总要求：产业兴旺、生态宜居、乡风文明、治理有效、生活富裕",
    "总目标：加快推进农业农村现代化",
])
add_card(s, 6.85, 4.05, 5.85, 2.35, "重大意义", [
    "建立健全城乡融合发展体制机制和政策体系",
    "举全党全社会之力推动乡村振兴，促进农业高质高效、乡村宜居宜业、农民富裕富足",
])
add_text(s, 0.65, 6.55, 12.0, 0.35,
         "资料依据：党的十九大报告、中央农村工作会议、《乡村全面振兴规划（2024－2027年）》等公开表述",
         10, GRAY)

# ---------- Slide 4: 20-character general requirements ----------
s = content_slide("二、总要求：二十字方针", 4, "GENERAL REQUIREMENTS")
reqs = [
    ("产业兴旺", "实现乡村振兴的基石", "夯实乡村发展的物质基础"),
    ("生态宜居", "提高乡村发展质量的保证", "建设绿色宜居的乡村环境"),
    ("乡风文明", "乡村建设的灵魂", "传承农耕文明与优秀乡土文化"),
    ("治理有效", "乡村善治的核心", "健全党组织领导的乡村治理体系"),
    ("生活富裕", "乡村振兴的目标", "拓宽农民增收致富渠道"),
]
for i, (title, role, desc) in enumerate(reqs):
    x = 0.5 + i * 2.52
    add_rect(s, x, 1.7, 2.35, 3.9, WHITE, rounded=True)
    add_rect(s, x, 1.7, 2.35, 0.1, GOLD)
    add_text(s, x + 0.1, 2.15, 2.15, 0.6, title, 21, GREEN, True,
             align=PP_ALIGN.CENTER)
    add_rect(s, x + 0.75, 2.95, 0.85, 0.03, GOLD)
    add_text(s, x + 0.2, 3.25, 1.95, 1.0, role, 13, DARK,
             align=PP_ALIGN.CENTER)
    add_text(s, x + 0.2, 4.5, 1.95, 0.9, desc, 12, GRAY,
             align=PP_ALIGN.CENTER)
add_rect(s, 0.65, 5.95, 12.05, 0.75, LIGHT_GREEN, rounded=True)
add_text(s, 0.95, 6.15, 11.4, 0.4,
         "党的十九大报告：按照产业兴旺、生态宜居、乡风文明、治理有效、生活富裕的总要求，加快推进农业农村现代化。",
         13, GREEN)

# ---------- Slide 5: Five revitalizations ----------
s = content_slide("三、五大振兴：协同推进", 5, "FIVE REVITALIZATIONS")
five = [
    ("产业振兴", "发展现代农业与乡村特色产业，夯实乡村振兴的物质基础"),
    ("人才振兴", "培育高素质农民，吸引各类人才返乡入乡创新创业"),
    ("文化振兴", "传承弘扬农耕文明与乡土文化，涵养文明乡风"),
    ("生态振兴", "整治农村人居环境，推进绿色低碳发展，建设生态宜居乡村"),
    ("组织振兴", "建强农村基层党组织，提升乡村治理效能与服务能力"),
]
positions = [(0.65, 1.55), (6.85, 1.55), (0.65, 3.5), (6.85, 3.5), (3.75, 5.45)]
for i, ((title, desc), (x, y)) in enumerate(zip(five, positions)):
    w = 5.85 if i < 4 else 5.85
    add_rect(s, x, y, w, 1.7, WHITE, rounded=True)
    add_rect(s, x, y, 0.1, 1.7, GREEN2)
    add_text(s, x + 0.35, y + 0.22, 1.0, 0.45, "0%d" % (i + 1), 18, GOLD, True)
    add_text(s, x + 1.35, y + 0.22, 4.2, 0.45, title, 19, GREEN, True)
    add_text(s, x + 0.35, y + 0.85, w - 0.7, 0.65, desc, 13, DARK)

# ---------- Slide 6: Three-step goals ----------
s = content_slide("四、“三步走”阶段性目标", 6, "MILESTONES")
add_rect(s, 1.3, 2.55, 10.7, 0.08, RGBColor(0xC8, 0xE6, 0xC9))
steps = [
    ("2020", "取得重要进展", "乡村振兴取得重要进展，制度框架和政策体系基本形成"),
    ("2035", "取得决定性进展", "乡村振兴取得决定性进展，农业农村现代化基本实现"),
    ("2050", "实现全面振兴", "乡村全面振兴，农业强、农村美、农民富全面实现"),
]
for i, (year, head, desc) in enumerate(steps):
    x = 0.65 + i * 4.15
    add_rect(s, x, 2.9, 3.85, 2.9, WHITE, rounded=True)
    add_rect(s, x, 2.9, 3.85, 0.1, GOLD)
    add_text(s, x + 0.3, 3.2, 3.25, 0.8, year, 36, GREEN, True,
             align=PP_ALIGN.CENTER)
    add_text(s, x + 0.3, 4.15, 3.25, 0.45, head, 18, GOLD, True,
             align=PP_ALIGN.CENTER)
    add_text(s, x + 0.35, 4.75, 3.15, 0.9, desc, 13, DARK,
             align=PP_ALIGN.CENTER)
add_text(s, 0.65, 6.2, 12.0, 0.4,
         "“三步走”方略与国家现代化进程相同步，是循序渐进、长期实施的战略安排。",
         13, GRAY, align=PP_ALIGN.CENTER)

# ---------- Slide 7: Key tasks ----------
s = content_slide("五、重点任务与实施路径", 7, "KEY TASKS")
tasks = [
    ("守住两条底线", "牢牢守住保障粮食安全和不发生规模性返贫致贫底线"),
    ("学习运用“千万工程”经验", "因地制宜建设宜居宜业和美乡村，提高强农惠农富农政策效能"),
    ("城乡融合发展", "重塑城乡关系，健全城乡融合发展体制机制和政策体系"),
    ("提升农业综合生产能力", "着力提升农业综合生产能力和质量效益，拓宽农民增收致富渠道"),
    ("坚持党管农村工作", "坚持农民主体地位，强化组织保障，提升乡村治理效能"),
    ("因地制宜、循序渐进", "坚持人与自然和谐共生，分类推进、不搞一刀切"),
]
for i, (title, desc) in enumerate(tasks):
    col, row = i % 2, i // 2
    x, y = 0.65 + col * 6.25, 1.5 + row * 1.72
    add_rect(s, x, y, 5.85, 1.5, WHITE, rounded=True)
    add_rect(s, x, y, 0.1, 1.5, GOLD)
    add_text(s, x + 0.35, y + 0.22, 5.2, 0.4, "%d  %s" % (i + 1, title), 16,
             GREEN, True)
    add_text(s, x + 0.35, y + 0.75, 5.2, 0.6, desc, 12.5, DARK)

# ---------- Slide 8: Closing ----------
s = prs.slides.add_slide(BLANK)
s.background.fill.solid()
s.background.fill.fore_color.rgb = GREEN
add_rect(s, 0, 0, 13.333, 0.12, GOLD)
add_text(s, 1.0, 2.15, 11.33, 1.0, "农业强 · 农村美 · 农民富", 44, WHITE,
         True, align=PP_ALIGN.CENTER)
add_rect(s, 5.42, 3.45, 2.5, 0.04, GOLD)
add_text(s, 1.0, 3.85, 11.33, 0.6,
         "扎实推进乡村全面振兴，让广大农民生活更加幸福美好", 20,
         RGBColor(0xC8, 0xE6, 0xC9), align=PP_ALIGN.CENTER)
add_text(s, 1.0, 6.4, 11.33, 0.5, "感谢聆听  ·  欢迎交流指正", 15, WHITE,
         align=PP_ALIGN.CENTER)

out = "乡村振兴主题汇报.pptx"
prs.save(out)

# verify the file is a readable pptx
check = Presentation(out)
print("Generated:", out)
print("Slides:", len(check.slides))
