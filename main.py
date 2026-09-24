# -*- coding: utf-8 -*-
import os
import sys
import sqlite3
import traceback
import logging
from datetime import datetime, timedelta
from calendar import monthrange

from kivy.config import Config
Config.set('kivy', 'window_icon', '')
Config.set('kivy', 'default_theme', 'atlas')
Config.set('kivy', 'background_color', 'ffffff')
Config.set('kivy', 'foreground_color', '000000')

from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.utils import platform
from kivy.metrics import dp, sp
from kivy.uix.dropdown import DropDown
from kivy.uix.behaviors import ButtonBehavior
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line
from kivy.properties import ListProperty, NumericProperty


# ============================================================
# ============== LOGGING SETUP ===============================
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("5MConcrete")


def safe_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        log.error("Error in %s: %s",
                  func.__name__ if hasattr(func, '__name__') else '?', e)
        log.error(traceback.format_exc())
        return None


# ============================================================
# ============ ANDROID PERMISSIONS & PATHS ===================
# ============================================================
if platform == 'android':
    try:
        from android.permissions import request_permissions, Permission
    except ImportError:
        request_permissions = None
        Permission = None

if platform == 'android':
    try:
        from android.storage import primary_external_storage_path
        ext_storage = primary_external_storage_path()
        if ext_storage:
            BASE_DIR = os.path.join(ext_storage, "Documents", "5M_Concrete_Reports")
        else:
            raise RuntimeError("No external storage available")
    except Exception as e:
        log.warning("External storage unavailable: %s", e)
        try:
            from android.storage import app_storage_path
            BASE_DIR = os.path.join(app_storage_path(), "5M_Concrete_Reports")
        except Exception:
            BASE_DIR = "/data/data/org.test.myapp/files"
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    os.makedirs(BASE_DIR, exist_ok=True)
except Exception as e:
    log.error("Cannot create BASE_DIR: %s", e)
    BASE_DIR = os.path.expanduser("~")

PDF_DIR = os.path.join(BASE_DIR, "reports")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
DB_PATH = os.path.join(BASE_DIR, "concrete.db")

for d in (PDF_DIR, BACKUP_DIR, ASSETS_DIR):
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        log.error("Cannot create %s: %s", d, e)


# ============================================================
# ============== REPORTLAB + ARABIC SUPPORT ==================
# ============================================================
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, PageBreak)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

if platform == 'android':
    import reportlab
    reportlab.rl_config.defaultPageSize = A4

ARABIC_RESHAPER_OK = False
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    ARABIC_RESHAPER_OK = True
except ImportError:
    log.info("arabic_reshaper / python-bidi not installed; "
             "Arabic text in PDF will appear as-is.")

ARABIC_FONT_OK = False
ARABIC_FONT_NAME = "Helvetica"
for _candidate in ("Amiri-Regular.ttf", "Cairo-Regular.ttf",
                   "NotoNaskhArabic-Regular.ttf", "Scheherazade-Regular.ttf"):
    _font_path = os.path.join(ASSETS_DIR, _candidate)
    if os.path.exists(_font_path):
        try:
            pdfmetrics.registerFont(TTFont("ArabicFont", _font_path))
            ARABIC_FONT_OK = True
            ARABIC_FONT_NAME = "ArabicFont"
            log.info("Arabic font loaded: %s", _candidate)
            break
        except Exception as e:
            log.warning("Cannot load font %s: %s", _candidate, e)


def ar(text):
    """يُهيّئ النص العربي لعرضه في PDF. آمن عند أي فشل."""
    if text is None:
        return ""
    text = str(text)
    if not text:
        return ""
    has_arabic = any('\u0600' <= c <= '\u06FF' for c in text)
    if has_arabic and ARABIC_RESHAPER_OK:
        try:
            return get_display(arabic_reshaper.reshape(text))
        except Exception as e:
            log.warning("Arabic reshape failed: %s", e)
    return text


# ============================================================
# ============ PDF STYLE HELPERS =============================
# ============================================================
PDF_COLORS = {
    'header_bg':    colors.HexColor('#1E5A99'),
    'header_text':  colors.HexColor('#FFFFFF'),
    'row_alt':      colors.HexColor('#F0F5FA'),
    'row_normal':   colors.HexColor('#FFFFFF'),
    'border':       colors.HexColor('#4A6E8A'),
    'title':        colors.HexColor('#1E5A99'),
    'subtitle':     colors.HexColor('#4A6E8A'),
    'success':      colors.HexColor('#2A9D5C'),
    'warning':      colors.HexColor('#F39C12'),
    'danger':       colors.HexColor('#D9363E'),
}


def P(text, size=8, align=TA_CENTER, color=None, bold=False):
    """
    إنشاء خلية Paragraph مع التفاف النص تلقائياً لمنع تداخل الأعمدة.
    تستخدم wordWrap='CJK' لضمان كسر النصوص الطويلة (حتى العربية) عند الحاجة.
    """
    try:
        style = ParagraphStyle(
            'CellStyle',
            fontName=ARABIC_FONT_NAME,
            fontSize=size,
            leading=size + 1.8,
            alignment=align,
            textColor=color if color is not None else colors.black,
            wordWrap='CJK',
            splitLongWords=1,
            spaceBefore=0,
            spaceAfter=0,
            leftIndent=0,
            rightIndent=0,
        )
        return Paragraph(ar(str(text)), style)
    except Exception as e:
        log.warning("P() failed: %s", e)
        return Paragraph("", ParagraphStyle('Fallback', fontName=ARABIC_FONT_NAME,
                                            fontSize=size))


def build_table_style(has_total_row=False):
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PDF_COLORS['header_bg']),
        ('TEXTCOLOR', (0, 0), (-1, 0), PDF_COLORS['header_text']),
        ('FONTNAME', (0, 0), (-1, 0), ARABIC_FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
        ('TOPPADDING', (0, 0), (-1, 0), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, PDF_COLORS['border']),
        ('BOX', (0, 0), (-1, -1), 1.2, PDF_COLORS['header_bg']),
        ('FONTNAME', (0, 1), (-1, -1), ARABIC_FONT_NAME),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ])
    if has_total_row:
        style.add('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#D6E4F0'))
    return style


def add_row_alternating(style, num_rows, start=1):
    for i in range(start, num_rows):
        if (i - start) % 2 == 1:
            style.add('BACKGROUND', (0, i), (-1, i), PDF_COLORS['row_alt'])


def make_report_title(text):
    return Paragraph(ar(text), ParagraphStyle(
        'ReportTitle', fontName=ARABIC_FONT_NAME,
        fontSize=16, textColor=PDF_COLORS['title'],
        alignment=TA_CENTER, spaceAfter=10))


def make_report_subtitle(text):
    return Paragraph(ar(text), ParagraphStyle(
        'ReportSub', fontName=ARABIC_FONT_NAME,
        fontSize=10, textColor=PDF_COLORS['subtitle'],
        alignment=TA_CENTER, spaceAfter=4))


def make_section_header(text):
    return Paragraph(ar(text), ParagraphStyle(
        'SectionHeader', fontName=ARABIC_FONT_NAME,
        fontSize=11, textColor=PDF_COLORS['header_bg'],
        spaceBefore=8, spaceAfter=6))


def make_signature():
    styles = getSampleStyleSheet()
    return [
        Spacer(1, 25),
        Paragraph("_" * 50, styles['Normal']),
        Paragraph(ar("Mohamed Ayoub - Production Manager"),
                  ParagraphStyle('Sig', parent=styles['Normal'],
                                 alignment=TA_RIGHT, fontName=ARABIC_FONT_NAME)),
        Paragraph(ar("Phone: 01028652869"),
                  ParagraphStyle('Phone', parent=styles['Normal'],
                                 alignment=TA_RIGHT, fontSize=9,
                                 fontName=ARABIC_FONT_NAME)),
        Paragraph(ar("Mohamed Samir Ayoub"),
                  ParagraphStyle('Name2', parent=styles['Normal'],
                                 alignment=TA_RIGHT, fontSize=9,
                                 fontName=ARABIC_FONT_NAME)),
    ]


def build_pdf_doc(filepath):
    """مستند PDF بهوامش صغيرة لاستغلال كامل عرض A4."""
    return SimpleDocTemplate(
        filepath, pagesize=A4,
        leftMargin=24, rightMargin=24,
        topMargin=36, bottomMargin=36,
        title="5M Report", author="5M System"
    )


# ============================================================
# ============== MODERN THEME ================================
# ============================================================
class Palette:
    BG          = (0.96, 0.97, 0.99, 1)
    SURFACE     = (1.00, 1.00, 1.00, 1)
    SURFACE_2   = (0.94, 0.95, 0.97, 1)
    PRIMARY     = (0.10, 0.35, 0.60, 1)
    PRIMARY_L   = (0.17, 0.48, 0.78, 1)
    PRIMARY_XL  = (0.85, 0.92, 0.99, 1)
    SUCCESS     = (0.16, 0.65, 0.38, 1)
    WARNING     = (0.95, 0.60, 0.10, 1)
    DANGER      = (0.85, 0.22, 0.24, 1)
    INFO        = (0.20, 0.60, 0.75, 1)
    NEUTRAL     = (0.45, 0.47, 0.52, 1)
    TEXT        = (0.10, 0.12, 0.16, 1)
    TEXT_MUTED  = (0.45, 0.47, 0.52, 1)
    TEXT_LIGHT  = (0.99, 0.99, 1.00, 1)
    BORDER      = (0.86, 0.88, 0.92, 1)

Window.clearcolor = Palette.BG


class Card(BoxLayout):
    radius = NumericProperty(dp(14))
    bg_color = ListProperty(Palette.SURFACE)
    border_color = ListProperty(Palette.BORDER)

    def __init__(self, **kw):
        kw.setdefault('padding', dp(14))
        kw.setdefault('spacing', dp(8))
        super().__init__(**kw)
        with self.canvas.before:
            self._bg = Color(*self.bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
            self._bc = Color(*self.border_color)
            self._bord = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
        self.bind(pos=self._update, size=self._update, bg_color=self._update_color)

    def _update(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._bord.pos = self.pos
        self._bord.size = self.size

    def _update_color(self, *a):
        self._bg.rgba = self.bg_color


class ModernButton(ButtonBehavior, Label):
    bg_color = ListProperty(Palette.PRIMARY)
    text_color = ListProperty(Palette.TEXT_LIGHT)
    radius = NumericProperty(dp(10))

    def __init__(self, **kw):
        kw.setdefault('font_size', sp(14))
        kw.setdefault('bold', True)
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(44))
        super().__init__(**kw)
        self.color = self.text_color
        with self.canvas.before:
            self._bg = Color(*self.bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
        self.bind(pos=self._update, size=self._update,
                  bg_color=self._update_color, text_color=self._update_text_color)

    def _update(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _update_color(self, *a):
        self._bg.rgba = self.bg_color

    def _update_text_color(self, *a):
        self.color = self.text_color

    def on_press(self):
        self.opacity = 0.75

    def on_release(self):
        self.opacity = 1.0


class ModernInput(TextInput):
    def __init__(self, **kw):
        kw.setdefault('font_size', sp(14))
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(46))
        kw.setdefault('multiline', False)
        kw.setdefault('padding', [dp(14), dp(12)])
        super().__init__(**kw)

        self.background_normal = ''
        self.background_active = ''
        self.background_color = (0, 0, 0, 0)
        self.foreground_color = (0.08, 0.10, 0.14, 1)
        self.cursor_color     = Palette.PRIMARY
        self.hint_text_color  = Palette.TEXT_MUTED
        self.selection_color  = (0.10, 0.35, 0.60, 0.35)

        with self.canvas.before:
            self._bg_color     = Color(*Palette.SURFACE)
            self._bg_rect      = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[dp(10)])
            self._border_color = Color(*Palette.BORDER)
            self._border_line  = Line(
                rounded_rectangle=(self.x, self.y,
                                    self.width, self.height, dp(10)),
                width=1.2)

        self.bind(pos=self._u, size=self._u)

    def _u(self, *a):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        self._border_line.rounded_rectangle = (
            self.x, self.y, self.width, self.height, dp(10))

    def on_focus(self, instance, value):
        self._border_color.rgba = Palette.PRIMARY if value else Palette.BORDER


class LightDropDown(DropDown):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_color = (1, 1, 1, 1)
        self.separator_color = (0.8, 0.8, 0.8, 1)
        self.separator_height = 1

    def add_widget(self, widget, index=0):
        if isinstance(widget, (Button, ButtonBehavior)):
            widget.background_color = (1, 1, 1, 1)
            widget.color = (0, 0, 0, 1)
            widget.background_normal = ''
        super().add_widget(widget, index)


class ModernSpinner(Spinner):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = Palette.SURFACE
        self.color = Palette.TEXT
        self.font_size = sp(13)
        self.size_hint_y = None
        self.height = dp(44)
        self.dropdown_cls = LightDropDown


class ModernHeader(BoxLayout):
    def __init__(self, title="5M System", subtitle="Production Management",
                 version="v2.0", **kw):
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(88))
        kw.setdefault('padding', [dp(16), dp(10)])
        kw.setdefault('spacing', dp(2))
        super().__init__(orientation='vertical', **kw)

        with self.canvas.before:
            self._c = Color(*Palette.PRIMARY)
            self._r = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._u, size=self._u)

        top = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        logo = Label(text="5M", font_size=sp(26), bold=True,
                     color=Palette.TEXT_LIGHT, size_hint_x=None, width=dp(60))
        title_box = BoxLayout(orientation='vertical')
        t1 = Label(text=title, font_size=sp(16), bold=True,
                   color=Palette.TEXT_LIGHT, halign='left', valign='bottom')
        t2 = Label(text=subtitle, font_size=sp(11),
                   color=(0.85, 0.90, 0.98, 1), halign='left', valign='top')
        for w in (t1, t2):
            w.bind(size=lambda i, v: setattr(i, 'text_size', (v[0], v[1])))
        title_box.add_widget(t1)
        title_box.add_widget(t2)

        top.add_widget(logo)
        top.add_widget(title_box)
        badge = Label(text=version, font_size=sp(10), bold=True,
                      color=Palette.TEXT_LIGHT, size_hint_x=None, width=dp(46))
        top.add_widget(badge)
        self.add_widget(top)
        self.add_widget(Label(text="Mohamed Ayoub  -  01028652869",
                              font_size=sp(9.5), color=(0.82, 0.88, 0.98, 1),
                              size_hint_y=None, height=dp(18)))

    def _u(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


class TabBar(BoxLayout):
    def __init__(self, tabs, on_select, **kw):
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(56))
        kw.setdefault('spacing', dp(4))
        kw.setdefault('padding', [dp(6), dp(6)])
        super().__init__(**kw)
        self._on_select = on_select
        self._tabs = tabs
        self._buttons = {}
        with self.canvas.before:
            self._c = Color(*Palette.SURFACE)
            self._r = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._u, size=self._u)
        for key, label in tabs:
            b = ModernButton(text=label, bg_color=Palette.SURFACE,
                             text_color=Palette.TEXT_MUTED,
                             font_size=sp(11), radius=dp(10))
            b.bind(on_release=lambda x, k=key: self._select(k))
            self.add_widget(b)
            self._buttons[key] = b

    def _u(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size

    def _select(self, key):
        self._apply_colors(key)
        if self._on_select:
            self._on_select(key)

    def _apply_colors(self, key):
        for k, b in self._buttons.items():
            if k == key:
                b.bg_color = Palette.PRIMARY
                b.text_color = Palette.TEXT_LIGHT
            else:
                b.bg_color = Palette.SURFACE
                b.text_color = Palette.TEXT_MUTED

    def set_active(self, key):
        if key in self._buttons:
            self._apply_colors(key)


class SiloBar(BoxLayout):
    progress = NumericProperty(0.0)
    bar_color = ListProperty(Palette.SUCCESS)
    radius = NumericProperty(dp(4))

    def __init__(self, **kw):
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(8))
        super().__init__(**kw)

        with self.canvas:
            Color(*Palette.SURFACE_2)
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[self.radius])
            self._fg_color = Color(*self.bar_color)
            self._fg = RoundedRectangle(
                pos=self.pos, size=(0, self.height), radius=[self.radius])

        self.bind(pos=self._refresh,
                  size=self._refresh,
                  progress=self._refresh,
                  bar_color=self._refresh_color)

    def _refresh_color(self, *a):
        self._fg_color.rgba = self.bar_color

    def _refresh(self, *a):
        self._bg.pos = self.pos
        self._bg.size = self.size
        pct = max(0.0, min(1.0, self.progress))
        w = self.width * pct
        self._fg.pos = self.pos
        self._fg.size = (w, self.height)


StyledButton = ModernButton
StyledTextInput = ModernInput
StyledSpinner = ModernSpinner


# ============================================================
# ==================== CONSTANTS =============================
# ============================================================
SILO_CAPACITIES = {1: 180000, 2: 180000, 3: 180000, 4: 180000}
SHIFT_START_HOUR = 8

MIN_QTY_PER_TRIP     = 0.1
MAX_QTY_PER_TRIP     = 50.0
MIN_CEMENT_PER_M3    = 50.0
MAX_CEMENT_PER_M3    = 1000.0
LOW_CEMENT_THRESHOLD = 20000

DEFAULT_MIX_COMPOSITION = {
    "Standard":         {"Sand": 800, "Agg1": 600, "Agg2": 500, "Water": 180, "Add1": 5, "Add2": 0},
    "High Strength":    {"Sand": 700, "Agg1": 700, "Agg2": 500, "Water": 160, "Add1": 8, "Add2": 3},
    "Lightweight":      {"Sand": 600, "Agg1": 500, "Agg2": 400, "Water": 190, "Add1": 4, "Add2": 2},
    "Self-Compacting":  {"Sand": 850, "Agg1": 650, "Agg2": 450, "Water": 200, "Add1": 10, "Add2": 5},
    "Fiber Reinforced": {"Sand": 750, "Agg1": 600, "Agg2": 500, "Water": 175, "Add1": 6, "Add2": 2}
}

ROLE_PERMISSIONS = {
    'admin': ['all'],
    'supervisor': ['all'],
    'operator': ['add_trip', 'search', 'cement_supply', 'view_reports']
}


# ============================================================
# ============== MEDIA SCANNER + FILE OPENER =================
# ============================================================
def notify_media_scanner(filepath):
    if platform == 'android' and os.path.exists(filepath):
        try:
            from jnius import autoclass
            MediaScannerConnection = autoclass('android.media.MediaScannerConnection')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            MediaScannerConnection.scanFile(PythonActivity.mActivity, [filepath], None, None)
        except Exception as e:
            log.warning("Media scanner notify failed: %s", e)


def open_file_location(filepath):
    if platform == 'android':
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            Uri = autoclass('android.net.Uri')
            File = autoclass('java.io.File')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            intent = Intent(Intent.ACTION_VIEW)
            uri = Uri.fromFile(File(filepath))
            intent.setDataAndType(uri, "application/pdf")
            intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            PythonActivity.mActivity.startActivity(intent)
        except Exception as e:
            log.error("Open file failed: %s", e)


# ============================================================
# =================== TIME FUNCTIONS =========================
# ============================================================
def get_shift_start_time(dt=None):
    if dt is None:
        dt = datetime.now()
    if dt.hour < SHIFT_START_HOUR:
        yesterday = dt - timedelta(days=1)
        return datetime(yesterday.year, yesterday.month, yesterday.day,
                        SHIFT_START_HOUR, 0, 0)
    return datetime(dt.year, dt.month, dt.day, SHIFT_START_HOUR, 0, 0)


def get_shift_date(dt=None):
    return get_shift_start_time(dt).strftime("%Y-%m-%d")


def get_shift_range_text(shift_date):
    try:
        start = datetime.strptime(shift_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    start_dt = datetime(start.year, start.month, start.day, SHIFT_START_HOUR, 0, 0)
    end_dt = start_dt + timedelta(hours=24)
    return f"{start_dt.strftime('%Y-%m-%d %H:%M')} - {end_dt.strftime('%Y-%m-%d %H:%M')}"


def get_previous_shift_date(shift_date):
    dt = datetime.strptime(shift_date, "%Y-%m-%d") - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def get_shift_type(dt=None):
    if dt is None:
        dt = datetime.now()
    start = get_shift_start_time(dt)
    return "Morning" if start.hour == SHIFT_START_HOUR else "Evening"


# ============================================================
# ======================= POPUPS =============================
# ============================================================
class LightPopup(Popup):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_color = (1, 1, 1, 1)
        self.title_color = (0, 0, 0, 1)
        self.separator_color = (0.8, 0.8, 0.8, 1)


class ProgressPopup(LightPopup):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = "Creating PDF..."
        self.size_hint = (0.6, 0.2)
        self.auto_dismiss = False
        layout = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(10))
        self.progress = ProgressBar(max=100)
        self.label = Label(text="0%", size_hint_y=None, height=dp(30), color=(0, 0, 0, 1))
        layout.add_widget(self.label)
        layout.add_widget(self.progress)
        self.content = layout

    def update_progress(self, value, text=None):
        try:
            self.progress.value = value
            self.label.text = text or f"{int(value)}%"
        except Exception:
            pass


# ============================================================
# ==================== DATABASE ==============================
# ============================================================
class Database:
    def __init__(self):
        self.conn = None

    def connect(self):
        if self.conn:
            return
        try:
            self._open(DB_PATH)
        except sqlite3.OperationalError as e:
            log.warning("Primary DB path failed (%s), trying fallback", e)
            alt_dir = os.path.join(
                os.environ.get('EXTERNAL_STORAGE', '/sdcard'),
                "5M_Concrete_Reports")
            try:
                os.makedirs(alt_dir, exist_ok=True)
            except Exception:
                alt_dir = os.path.expanduser("~")
            alt_db = os.path.join(alt_dir, "concrete.db")
            self._open(alt_db)

    def _open(self, path):
        self.conn = sqlite3.connect(path, timeout=20)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._create_indexes()
        self._ensure_initial_data()

    def disconnect(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception as e:
                log.warning("DB close error: %s", e)
            self.conn = None

    def _create_tables(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS plants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            location TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id INTEGER NOT NULL DEFAULT 1,
            date TEXT, time TEXT, shift_date TEXT, shift_type TEXT,
            customer TEXT, truck TEXT, driver TEXT, mix TEXT,
            quantity REAL, cement REAL, sand REAL, agg1 REAL, agg2 REAL,
            water REAL, add1 REAL, add2 REAL,
            silo INTEGER, pump TEXT, pump_operator TEXT,
            secondary_silo INTEGER DEFAULT NULL,
            secondary_cement REAL DEFAULT 0,
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS cement_supplies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id INTEGER NOT NULL DEFAULT 1,
            date TEXT, time TEXT, shift_date TEXT,
            silo INTEGER, amount REAL, notes TEXT,
            cement_type TEXT DEFAULT '',
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS customer_consumption (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id INTEGER NOT NULL DEFAULT 1,
            customer TEXT, year INTEGER, month INTEGER,
            concrete REAL, cement REAL, sand REAL, agg1 REAL, agg2 REAL,
            water REAL, add1 REAL, add2 REAL,
            UNIQUE(plant_id, customer, year, month),
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS silo_balances (
            shift_date TEXT,
            plant_id INTEGER NOT NULL DEFAULT 1,
            silo1 REAL DEFAULT 0, silo2 REAL DEFAULT 0,
            silo3 REAL DEFAULT 0, silo4 REAL DEFAULT 0,
            PRIMARY KEY (shift_date, plant_id),
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS shift_start_balances (
            shift_date TEXT,
            plant_id INTEGER NOT NULL DEFAULT 1,
            silo1 REAL DEFAULT 0, silo2 REAL DEFAULT 0,
            silo3 REAL DEFAULT 0, silo4 REAL DEFAULT 0,
            PRIMARY KEY (shift_date, plant_id),
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        )''')
        c.execute('CREATE TABLE IF NOT EXISTS drivers (id INTEGER PRIMARY KEY, name TEXT UNIQUE, phone TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS trucks (id INTEGER PRIMARY KEY, number TEXT UNIQUE, model TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name TEXT UNIQUE, phone TEXT, address TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS mix_types (name TEXT PRIMARY KEY)')
        c.execute('''CREATE TABLE IF NOT EXISTS mix_compositions (
            mix_name TEXT PRIMARY KEY,
            sand REAL, agg1 REAL, agg2 REAL, water REAL, add1 REAL, add2 REAL,
            FOREIGN KEY (mix_name) REFERENCES mix_types(name)
        )''')
        c.execute('CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, username TEXT, action TEXT, details TEXT)')
        self.conn.commit()
        self._migrate_tables()

    def _create_indexes(self):
        c = self.conn.cursor()
        c.execute("CREATE INDEX IF NOT EXISTS idx_trips_plant_shift ON trips(plant_id, shift_date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trips_customer ON trips(customer)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trips_date ON trips(date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trips_datetime ON trips(date, time)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trips_pump ON trips(pump)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_supplies_plant_shift ON cement_supplies(plant_id, shift_date)")
        self.conn.commit()

    def _migrate_tables(self):
        c = self.conn.cursor()
        for table in ('trips', 'cement_supplies', 'customer_consumption',
                      'silo_balances', 'shift_start_balances'):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN plant_id INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass
        for col_def in (
            "ALTER TABLE trips ADD COLUMN secondary_silo INTEGER DEFAULT NULL",
            "ALTER TABLE trips ADD COLUMN secondary_cement REAL DEFAULT 0",
            "ALTER TABLE cement_supplies ADD COLUMN cement_type TEXT DEFAULT ''",
        ):
            try:
                c.execute(col_def)
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def _ensure_initial_data(self):
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM plants")
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO plants (name, location) VALUES (?, ?)",
                      ("Main Plant", "Default Location"))
        c.execute("SELECT COUNT(*) FROM mix_types")
        if c.fetchone()[0] == 0:
            for mix_name, comp in DEFAULT_MIX_COMPOSITION.items():
                c.execute("INSERT INTO mix_types (name) VALUES (?)", (mix_name,))
                c.execute(
                    "INSERT INTO mix_compositions (mix_name, sand, agg1, agg2, water, add1, add2) VALUES (?,?,?,?,?,?,?)",
                    (mix_name, comp["Sand"], comp["Agg1"], comp["Agg2"],
                     comp["Water"], comp["Add1"], comp["Add2"]))
        c.execute("SELECT COUNT(*) FROM trucks")
        if c.fetchone()[0] == 0:
            for num in range(201, 240):
                c.execute("INSERT INTO trucks (number, model) VALUES (?, ?)",
                          (str(num), "Standard"))
        c.execute("SELECT COUNT(*) FROM drivers")
        if c.fetchone()[0] == 0:
            default_drivers = sorted([
                "Ahmed El-Said", "Ahmed Gamal", "Ahmed Saleh", "Ahmed Abdelaziz",
                "Mohamed Abdelaziz", "Sayed Abdallah", "Sayed Ragab",
                "Mohy Ibrahim", "Emad Talaba"
            ])
            for name in default_drivers:
                c.execute("INSERT INTO drivers (name, phone) VALUES (?, ?)", (name, ""))
        self.conn.commit()

    # ---------------- Plants ----------------
    def get_plants(self):
        c = self.conn.cursor()
        c.execute("SELECT id, name, location FROM plants ORDER BY name")
        return [dict(r) for r in c.fetchall()]

    def add_plant(self, name, location=""):
        try:
            c = self.conn.cursor()
            c.execute("INSERT INTO plants (name, location) VALUES (?, ?)",
                      (name, location))
            self.conn.commit()
            return c.lastrowid
        except sqlite3.IntegrityError:
            return None

    def update_plant(self, plant_id, name, location):
        try:
            c = self.conn.cursor()
            c.execute("UPDATE plants SET name=?, location=? WHERE id=?",
                      (name, location, plant_id))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def delete_plant(self, plant_id):
        c = self.conn.cursor()
        for table, label in (('trips', 'trips'),
                             ('cement_supplies', 'cement supplies'),
                             ('silo_balances', 'silo balances')):
            c.execute(f"SELECT COUNT(*) FROM {table} WHERE plant_id=?", (plant_id,))
            if c.fetchone()[0] > 0:
                return False, f"Cannot delete plant with existing {label}."
        c.execute("DELETE FROM plants WHERE id=?", (plant_id,))
        self.conn.commit()
        return True, "Plant deleted successfully"

    # ---------------- Audit ----------------
    def log_action(self, username, action, details=""):
        try:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO audit_log (timestamp, username, action, details) VALUES (?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 username, action, details))
            self.conn.commit()
        except Exception as e:
            log.warning("log_action failed: %s", e)

    def _log_in_txn(self, cursor, username, action, details=""):
        cursor.execute(
            "INSERT INTO audit_log (timestamp, username, action, details) VALUES (?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             username, action, details))

    # ---------------- Trips ----------------
    def add_trip(self, trip):
        required = ('plant_id', 'date', 'time', 'shift_date', 'shift_type',
                    'customer', 'truck', 'driver', 'mix', 'quantity',
                    'cement', 'silo')
        missing = [k for k in required if k not in trip]
        if missing:
            raise ValueError(f"Missing required trip fields: {missing}")

        c = self.conn.cursor()
        c.execute('''INSERT INTO trips 
            (plant_id, date, time, shift_date, shift_type, customer, truck, driver, mix,
             quantity, cement, sand, agg1, agg2, water, add1, add2,
             silo, pump, pump_operator, secondary_silo, secondary_cement)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (trip['plant_id'], trip['date'], trip['time'], trip['shift_date'],
             trip['shift_type'], trip['customer'], trip['truck'], trip['driver'],
             trip['mix'], trip['quantity'], trip['cement'],
             trip.get('sand', 0), trip.get('agg1', 0), trip.get('agg2', 0),
             trip.get('water', 0), trip.get('add1', 0), trip.get('add2', 0),
             trip['silo'], trip.get('pump', ''), trip.get('pump_operator', ''),
             trip.get('secondary_silo'), trip.get('secondary_cement', 0)))
        self.conn.commit()
        self._update_customer_consumption(trip)
        return c.lastrowid

    def _update_customer_consumption(self, trip):
        c = self.conn.cursor()
        dt = datetime.strptime(trip['date'], "%Y-%m-%d")
        year, month = dt.year, dt.month
        c.execute('''INSERT INTO customer_consumption 
            (plant_id, customer, year, month, concrete, cement, sand, agg1, agg2, water, add1, add2)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(plant_id, customer, year, month) DO UPDATE SET
            concrete = concrete + excluded.concrete,
            cement = cement + excluded.cement,
            sand = sand + excluded.sand,
            agg1 = agg1 + excluded.agg1,
            agg2 = agg2 + excluded.agg2,
            water = water + excluded.water,
            add1 = add1 + excluded.add1,
            add2 = add2 + excluded.add2''',
            (trip['plant_id'], trip['customer'], year, month, trip['quantity'],
             trip['cement'] + trip.get('secondary_cement', 0),
             trip.get('sand', 0), trip.get('agg1', 0), trip.get('agg2', 0),
             trip.get('water', 0), trip.get('add1', 0), trip.get('add2', 0)))
        self.conn.commit()

    def get_trips(self, plant_id=None, shift_date=None, customer=None, truck=None,
                  driver=None, mix=None, pump=None, year=None, month=None,
                  start_datetime=None, end_datetime=None, limit=None):
        c = self.conn.cursor()
        query = "SELECT * FROM trips WHERE 1=1"
        params = []
        if plant_id is not None:
            query += " AND plant_id=?"; params.append(plant_id)
        if shift_date:
            query += " AND shift_date=?"; params.append(shift_date)
        if customer:
            query += " AND customer LIKE ?"; params.append(f"%{customer}%")
        if truck:
            query += " AND truck LIKE ?"; params.append(f"%{truck}%")
        if driver:
            query += " AND driver LIKE ?"; params.append(f"%{driver}%")
        if mix:
            query += " AND mix=?"; params.append(mix)
        if pump:
            query += " AND pump LIKE ?"; params.append(f"%{pump}%")
        if year:
            query += " AND strftime('%Y', date)=?"; params.append(str(year))
        if month:
            query += " AND strftime('%m', date)=?"; params.append(f"{month:02d}")
        if start_datetime:
            query += " AND (date || ' ' || time) >= ?"; params.append(start_datetime)
        if end_datetime:
            query += " AND (date || ' ' || time) <= ?"; params.append(end_datetime)
        query += " ORDER BY date, time"
        if limit is not None:
            query += " LIMIT ?"; params.append(limit)
        c.execute(query, params)
        return [dict(r) for r in c.fetchall()]

    def _subtract_consumption(self, cursor, plant_id, customer, year, month, trip_data):
        total_cement = trip_data['cement'] + (trip_data.get('secondary_cement') or 0)
        cursor.execute('''
            UPDATE customer_consumption SET
                concrete = concrete - ?,
                cement = cement - ?,
                sand = sand - ?,
                agg1 = agg1 - ?,
                agg2 = agg2 - ?,
                water = water - ?,
                add1 = add1 - ?,
                add2 = add2 - ?
            WHERE plant_id = ? AND customer = ? AND year = ? AND month = ?
        ''', (
            trip_data['quantity'], total_cement,
            trip_data.get('sand', 0), trip_data.get('agg1', 0), trip_data.get('agg2', 0),
            trip_data.get('water', 0), trip_data.get('add1', 0), trip_data.get('add2', 0),
            plant_id, customer, year, month))

    def _add_consumption(self, cursor, plant_id, customer, year, month, trip_data):
        total_cement = trip_data['cement'] + (trip_data.get('secondary_cement') or 0)
        cursor.execute('''
            INSERT INTO customer_consumption
                (plant_id, customer, year, month, concrete, cement, sand, agg1, agg2, water, add1, add2)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(plant_id, customer, year, month) DO UPDATE SET
                concrete = concrete + excluded.concrete,
                cement = cement + excluded.cement,
                sand = sand + excluded.sand,
                agg1 = agg1 + excluded.agg1,
                agg2 = agg2 + excluded.agg2,
                water = water + excluded.water,
                add1 = add1 + excluded.add1,
                add2 = add2 + excluded.add2
        ''', (
            plant_id, customer, year, month,
            trip_data['quantity'], total_cement,
            trip_data.get('sand', 0), trip_data.get('agg1', 0), trip_data.get('agg2', 0),
            trip_data.get('water', 0), trip_data.get('add1', 0), trip_data.get('add2', 0)))

    def _recalc_shift_balance_in_txn(self, cursor, plant_id, shift_date):
        cursor.execute("""
            SELECT DISTINCT shift_date FROM (
                SELECT shift_date FROM trips WHERE plant_id = ?
                UNION 
                SELECT shift_date FROM cement_supplies WHERE plant_id = ?
            ) WHERE shift_date >= ?
            ORDER BY shift_date
        """, (plant_id, plant_id, shift_date))
        shifts = [row[0] for row in cursor.fetchall()]
        for shift in shifts:
            prev_shift = get_previous_shift_date(shift)
            cursor.execute(
                "SELECT silo1,silo2,silo3,silo4 FROM silo_balances WHERE shift_date=? AND plant_id=?",
                (prev_shift, plant_id))
            row = cursor.fetchone()
            start_balance = ([row['silo1'], row['silo2'], row['silo3'], row['silo4']]
                             if row else [0, 0, 0, 0])
            cursor.execute(
                "INSERT OR REPLACE INTO shift_start_balances (shift_date, plant_id, silo1,silo2,silo3,silo4) VALUES (?,?,?,?,?,?)",
                (shift, plant_id, start_balance[0], start_balance[1],
                 start_balance[2], start_balance[3]))
            balances = list(start_balance)
            cursor.execute(
                "SELECT silo, SUM(amount) FROM cement_supplies WHERE plant_id=? AND shift_date=? GROUP BY silo",
                (plant_id, shift))
            for row_sup in cursor.fetchall():
                idx = row_sup[0] - 1
                if 0 <= idx < 4:
                    balances[idx] += row_sup[1]
            cursor.execute(
                "SELECT silo, secondary_silo, cement, secondary_cement FROM trips WHERE plant_id=? AND shift_date=?",
                (plant_id, shift))
            for row_trip in cursor.fetchall():
                idx = row_trip['silo'] - 1
                if 0 <= idx < 4:
                    balances[idx] -= row_trip['cement']
                if row_trip['secondary_silo'] and row_trip['secondary_cement']:
                    idx2 = row_trip['secondary_silo'] - 1
                    if 0 <= idx2 < 4:
                        balances[idx2] -= row_trip['secondary_cement']
            cursor.execute(
                "INSERT OR REPLACE INTO silo_balances (shift_date, plant_id, silo1,silo2,silo3,silo4) VALUES (?,?,?,?,?,?)",
                (shift, plant_id, balances[0], balances[1], balances[2], balances[3]))

    def update_trip(self, trip_id, updates, current_user=None):
        c = self.conn.cursor()
        c.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
        original = c.fetchone()
        if not original:
            return False, "Trip not found"
        original = dict(original)

        allowed = {'quantity', 'cement', 'mix', 'truck', 'driver', 'pump', 'silo',
                   'sand', 'agg1', 'agg2', 'water', 'add1', 'add2', 'customer',
                   'secondary_silo', 'secondary_cement', 'plant_id'}
        safe_updates = {k: v for k, v in updates.items() if k in allowed}
        if not safe_updates:
            return False, "No valid fields to update"

        new_values = {}
        for field in allowed:
            new_values[field] = (safe_updates[field] if field in safe_updates
                                 else original.get(field, 0))

        if 'quantity' in safe_updates or 'mix' in safe_updates:
            qty = new_values['quantity']
            mix_name = new_values['mix']
            comp = self.get_mix_composition(mix_name)
            if original['quantity'] != 0:
                cement_per_m3 = ((original['cement'] +
                                  (original.get('secondary_cement') or 0)) /
                                 original['quantity'])
            else:
                cement_per_m3 = 350
            total_cement = qty * cement_per_m3
            if original.get('secondary_silo'):
                denom = original['cement'] + original['secondary_cement']
                ratio = (original['cement'] / denom) if denom > 0 else 1
                new_values['cement'] = total_cement * ratio
                new_values['secondary_cement'] = total_cement - new_values['cement']
            else:
                new_values['cement'] = total_cement
                new_values['secondary_cement'] = 0
            new_values['sand'] = qty * comp['Sand']
            new_values['agg1'] = qty * comp['Agg1']
            new_values['agg2'] = qty * comp['Agg2']
            new_values['water'] = qty * comp['Water']
            new_values['add1'] = qty * comp['Add1']
            new_values['add2'] = qty * comp['Add2']
            for k in ('cement', 'secondary_cement', 'sand', 'agg1',
                      'agg2', 'water', 'add1', 'add2'):
                safe_updates[k] = new_values[k]

        try:
            self.conn.execute("BEGIN IMMEDIATE")
            old_plant = original['plant_id']
            old_cust = original['customer']
            old_date = datetime.strptime(original['date'], "%Y-%m-%d")
            self._subtract_consumption(c, old_plant, old_cust,
                                        old_date.year, old_date.month, original)
            self._add_consumption(c, new_values['plant_id'],
                                   new_values['customer'],
                                   old_date.year, old_date.month, new_values)
            set_clause = ", ".join(f"{k}=?" for k in safe_updates.keys())
            params = list(safe_updates.values()) + [trip_id]
            c.execute(f"UPDATE trips SET {set_clause} WHERE id=?", params)

            self._recalc_shift_balance_in_txn(c, original['plant_id'],
                                              original['shift_date'])
            if new_values['plant_id'] != original['plant_id']:
                self._recalc_shift_balance_in_txn(c, new_values['plant_id'],
                                                  original['shift_date'])

            changes = []
            for field in safe_updates:
                old_val = original.get(field)
                new_val = safe_updates[field]
                if old_val != new_val:
                    changes.append(f"{field}: {old_val} -> {new_val}")
            self._log_in_txn(c, current_user or "system", "edit_trip",
                             f"Trip {trip_id}: " + ", ".join(changes))
            self.conn.commit()
            return True, f"Trip {trip_id} updated successfully"
        except Exception as e:
            self.conn.rollback()
            log.error("update_trip failed: %s", e)
            log.error(traceback.format_exc())
            return False, str(e)

    def delete_trip(self, trip_id, current_user=None):
        c = self.conn.cursor()
        c.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
        original = c.fetchone()
        if not original:
            return False, "Trip not found"
        original = dict(original)

        try:
            self.conn.execute("BEGIN IMMEDIATE")
            old_date = datetime.strptime(original['date'], "%Y-%m-%d")
            self._subtract_consumption(c, original['plant_id'],
                                        original['customer'],
                                        old_date.year, old_date.month, original)
            c.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
            self._recalc_shift_balance_in_txn(c, original['plant_id'],
                                              original['shift_date'])
            self._log_in_txn(c, current_user or "system", "delete_trip",
                             f"Trip {trip_id} deleted")
            self.conn.commit()
            return True, f"Trip {trip_id} deleted"
        except Exception as e:
            self.conn.rollback()
            log.error("delete_trip failed: %s", e)
            return False, str(e)

    def get_customer_consumption_period(self, start_datetime, end_datetime,
                                        plant_id=None, customer=None):
        c = self.conn.cursor()
        query = """
            SELECT 
                customer,
                SUM(quantity) as total_concrete,
                SUM(cement + COALESCE(secondary_cement, 0)) as total_cement,
                SUM(sand) as total_sand,
                SUM(agg1) as total_agg1,
                SUM(agg2) as total_agg2,
                SUM(water) as total_water,
                SUM(add1) as total_add1,
                SUM(add2) as total_add2,
                COUNT(*) as trip_count
            FROM trips
            WHERE (date || ' ' || time) >= ? AND (date || ' ' || time) <= ?
        """
        params = [start_datetime, end_datetime]
        if plant_id is not None:
            query += " AND plant_id = ?"; params.append(plant_id)
        if customer:
            query += " AND customer = ?"; params.append(customer)
        query += " GROUP BY customer ORDER BY total_concrete DESC"
        c.execute(query, params)
        return [dict(r) for r in c.fetchall()]

    # ---------------- Mix compositions ----------------
    def get_mix_composition(self, mix_name):
        c = self.conn.cursor()
        c.execute(
            "SELECT sand, agg1, agg2, water, add1, add2 FROM mix_compositions WHERE mix_name=?",
            (mix_name,))
        row = c.fetchone()
        if row:
            return {"Sand": row['sand'], "Agg1": row['agg1'], "Agg2": row['agg2'],
                    "Water": row['water'], "Add1": row['add1'], "Add2": row['add2']}
        return DEFAULT_MIX_COMPOSITION.get(mix_name, DEFAULT_MIX_COMPOSITION["Standard"])

    def update_mix_composition(self, mix_name, composition):
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO mix_compositions (mix_name, sand, agg1, agg2, water, add1, add2) VALUES (?,?,?,?,?,?,?)",
            (mix_name, composition['Sand'], composition['Agg1'],
             composition['Agg2'], composition['Water'],
             composition['Add1'], composition['Add2']))
        self.conn.commit()

    # ---------------- Cement supplies ----------------
    def add_cement_supply(self, supply):
        c = self.conn.cursor()
        c.execute('''INSERT INTO cement_supplies 
            (plant_id, date, time, shift_date, silo, amount, notes, cement_type)
            VALUES (?,?,?,?,?,?,?,?)''',
            (supply['plant_id'], supply['date'], supply['time'], supply['shift_date'],
             supply['silo'], supply['amount'], supply.get('notes', ''),
             supply.get('cement_type', '')))
        self.conn.commit()
        return c.lastrowid

    def get_cement_supplies(self, plant_id=None, shift_date=None, silo=None):
        c = self.conn.cursor()
        query = "SELECT * FROM cement_supplies WHERE 1=1"
        params = []
        if plant_id is not None:
            query += " AND plant_id=?"; params.append(plant_id)
        if shift_date:
            query += " AND shift_date=?"; params.append(shift_date)
        if silo:
            query += " AND silo=?"; params.append(silo)
        query += " ORDER BY date DESC, time DESC"
        c.execute(query, params)
        return [dict(r) for r in c.fetchall()]

    def get_latest_cement_type(self, plant_id, silo):
        c = self.conn.cursor()
        c.execute(
            "SELECT cement_type FROM cement_supplies WHERE plant_id=? AND silo=? AND cement_type != '' ORDER BY date DESC, time DESC LIMIT 1",
            (plant_id, silo))
        row = c.fetchone()
        return row['cement_type'] if row else None

    def undo_last_cement_supply(self, plant_id, shift_date):
        c = self.conn.cursor()
        c.execute(
            "SELECT id FROM cement_supplies WHERE plant_id=? AND shift_date=? ORDER BY id DESC LIMIT 1",
            (plant_id, shift_date))
        row = c.fetchone()
        if not row:
            return False
        c.execute("DELETE FROM cement_supplies WHERE id=?", (row['id'],))
        self.conn.commit()
        return True

    # ---------------- Silo balances ----------------
    def get_shift_start_balance(self, plant_id, shift_date):
        c = self.conn.cursor()
        c.execute(
            "SELECT silo1,silo2,silo3,silo4 FROM shift_start_balances WHERE plant_id=? AND shift_date=?",
            (plant_id, shift_date))
        row = c.fetchone()
        return [row['silo1'], row['silo2'], row['silo3'], row['silo4']] if row else None

    def save_shift_start_balance(self, plant_id, shift_date, balances):
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO shift_start_balances (plant_id, shift_date, silo1, silo2, silo3, silo4) VALUES (?,?,?,?,?,?)",
            (plant_id, shift_date, balances[0], balances[1], balances[2], balances[3]))
        self.conn.commit()

    def get_end_balance(self, plant_id, shift_date):
        c = self.conn.cursor()
        c.execute(
            "SELECT silo1,silo2,silo3,silo4 FROM silo_balances WHERE plant_id=? AND shift_date=?",
            (plant_id, shift_date))
        row = c.fetchone()
        return [row['silo1'], row['silo2'], row['silo3'], row['silo4']] if row else None

    def save_end_balance(self, plant_id, shift_date, balances):
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO silo_balances (plant_id, shift_date, silo1, silo2, silo3, silo4) VALUES (?,?,?,?,?,?)",
            (plant_id, shift_date, balances[0], balances[1], balances[2], balances[3]))
        self.conn.commit()

    def calculate_balance(self, plant_id, shift_date):
        start = self.get_shift_start_balance(plant_id, shift_date)
        if start is None:
            prev = get_previous_shift_date(shift_date)
            prev_end = self.get_end_balance(plant_id, prev)
            if prev_end is not None:
                start = prev_end.copy()
                self.save_shift_start_balance(plant_id, shift_date, start)
            else:
                start = [0, 0, 0, 0]
        balances = list(start)
        for s in self.get_cement_supplies(plant_id=plant_id, shift_date=shift_date):
            if 1 <= s['silo'] <= 4:
                balances[s['silo'] - 1] += s['amount']
        for t in self.get_trips(plant_id=plant_id, shift_date=shift_date):
            if 1 <= t['silo'] <= 4:
                balances[t['silo'] - 1] -= t['cement']
            if t.get('secondary_silo') and t.get('secondary_cement'):
                if 1 <= t['secondary_silo'] <= 4:
                    balances[t['secondary_silo'] - 1] -= t['secondary_cement']
        self.save_end_balance(plant_id, shift_date, balances)
        return balances

    def recalculate_balances_from_shift(self, plant_id, from_shift_date):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            c = self.conn.cursor()
            c.execute("""
                SELECT DISTINCT shift_date FROM (
                    SELECT shift_date FROM trips WHERE plant_id = ?
                    UNION 
                    SELECT shift_date FROM cement_supplies WHERE plant_id = ?
                ) WHERE shift_date >= ?
                ORDER BY shift_date
            """, (plant_id, plant_id, from_shift_date))
            shifts = [row[0] for row in c.fetchall()]
            for shift in shifts:
                prev_shift = get_previous_shift_date(shift)
                prev_end = self.get_end_balance(plant_id, prev_shift)
                start_balance = prev_end if prev_end else [0, 0, 0, 0]
                self.save_shift_start_balance(plant_id, shift, start_balance)
                balances = list(start_balance)
                for s in self.get_cement_supplies(plant_id=plant_id, shift_date=shift):
                    if 1 <= s['silo'] <= 4:
                        balances[s['silo'] - 1] += s['amount']
                for t in self.get_trips(plant_id=plant_id, shift_date=shift):
                    if 1 <= t['silo'] <= 4:
                        balances[t['silo'] - 1] -= t['cement']
                    if t.get('secondary_silo') and t.get('secondary_cement'):
                        if 1 <= t['secondary_silo'] <= 4:
                            balances[t['secondary_silo'] - 1] -= t['secondary_cement']
                self.save_end_balance(plant_id, shift, balances)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            log.error("recalculate_balances_from_shift failed: %s", e)
            raise

    def set_initial_balances(self, plant_id, shift_date, balances):
        self.save_shift_start_balance(plant_id, shift_date, balances)
        self.calculate_balance(plant_id, shift_date)
        self.recalculate_balances_from_shift(plant_id, shift_date)

    def reset_silo_balance(self, plant_id, silo, new_balance, shift_date):
        start = self.get_shift_start_balance(plant_id, shift_date) or [0, 0, 0, 0]
        start = list(start)
        start[silo - 1] = new_balance
        self.save_shift_start_balance(plant_id, shift_date, start)
        self.calculate_balance(plant_id, shift_date)
        self.recalculate_balances_from_shift(plant_id, shift_date)

    # ---------------- Mix types ----------------
    def add_custom_mix(self, name, sand, agg1, agg2, water, add1, add2):
        try:
            c = self.conn.cursor()
            c.execute("INSERT INTO mix_types (name) VALUES (?)", (name,))
            c.execute(
                "INSERT INTO mix_compositions (mix_name, sand, agg1, agg2, water, add1, add2) VALUES (?,?,?,?,?,?,?)",
                (name, sand, agg1, agg2, water, add1, add2))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def delete_all_custom_mixes(self):
        c = self.conn.cursor()
        c.execute("PRAGMA foreign_keys = OFF")
        try:
            c.execute("SELECT name FROM mix_types WHERE name != 'Standard'")
            custom_mixes = c.fetchall()
            for row in custom_mixes:
                mix_name = row['name']
                c.execute("UPDATE trips SET mix = 'Standard' WHERE mix = ?", (mix_name,))
                c.execute("DELETE FROM mix_compositions WHERE mix_name=?", (mix_name,))
                c.execute("DELETE FROM mix_types WHERE name=?", (mix_name,))
            self.conn.commit()
            return len(custom_mixes)
        finally:
            c.execute("PRAGMA foreign_keys = ON")

    # ---------------- Drivers ----------------
    def get_drivers(self):
        c = self.conn.cursor()
        c.execute("SELECT id, name, phone FROM drivers ORDER BY name")
        return [dict(r) for r in c.fetchall()]

    def add_driver(self, name, phone):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO drivers (name, phone) VALUES (?,?)",
                  (name, phone))
        self.conn.commit()
        return c.lastrowid

    def delete_driver(self, driver_id):
        c = self.conn.cursor()
        c.execute("DELETE FROM drivers WHERE id=?", (driver_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ---------------- Trucks ----------------
    def get_trucks(self):
        c = self.conn.cursor()
        c.execute("SELECT id, number, model FROM trucks ORDER BY number")
        return [dict(r) for r in c.fetchall()]

    def add_truck(self, number, model):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO trucks (number, model) VALUES (?,?)",
                  (number, model))
        self.conn.commit()
        return c.lastrowid

    def delete_truck(self, truck_id):
        c = self.conn.cursor()
        c.execute("DELETE FROM trucks WHERE id=?", (truck_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ---------------- Customers ----------------
    def get_customers(self):
        c = self.conn.cursor()
        c.execute("SELECT id, name, phone, address FROM customers ORDER BY name")
        return [dict(r) for r in c.fetchall()]

    def add_customer(self, name, phone, address=""):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO customers (name, phone, address) VALUES (?,?,?)",
                  (name, phone, address))
        self.conn.commit()
        return c.lastrowid

    def delete_customer(self, customer_id):
        c = self.conn.cursor()
        c.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ---------------- Mix types list ----------------
    def get_mix_types(self):
        c = self.conn.cursor()
        c.execute("SELECT name FROM mix_types ORDER BY name")
        return [row['name'] for row in c.fetchall()]

    def add_mix_type(self, name):
        try:
            c = self.conn.cursor()
            c.execute("INSERT INTO mix_types (name) VALUES (?)", (name,))
            c.execute(
                "INSERT INTO mix_compositions (mix_name, sand, agg1, agg2, water, add1, add2) VALUES (?,?,?,?,?,?,?)",
                (name, 800, 600, 500, 180, 5, 0))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            log.error("add_mix_type failed: %s", e)
            return False

    def delete_mix_type(self, name):
        if name == "Standard":
            return False
        c = self.conn.cursor()
        c.execute("PRAGMA foreign_keys = OFF")
        try:
            c.execute("UPDATE trips SET mix = 'Standard' WHERE mix = ?", (name,))
            c.execute("DELETE FROM mix_compositions WHERE mix_name = ?", (name,))
            c.execute("DELETE FROM mix_types WHERE name = ?", (name,))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            log.error("delete_mix_type failed: %s", e)
            return False
        finally:
            c.execute("PRAGMA foreign_keys = ON")

    # ---------------- Backup ----------------
    def backup_database(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"concrete_backup_{timestamp}.db")
        backup_conn = sqlite3.connect(backup_path)
        try:
            self.conn.backup(backup_conn)
        finally:
            backup_conn.close()
        return backup_path


# ============================================================
# ================= DATE PICKER POPUP ========================
# ============================================================
class DatePickerPopup(LightPopup):
    def __init__(self, on_select, **kwargs):
        super().__init__(title="Select Date", size_hint=(0.8, 0.6), **kwargs)
        self.on_select = on_select
        self.selected_day = datetime.now().day
        layout = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        year_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        year_box.add_widget(Label(text="Year:", color=(0, 0, 0, 1)))
        self.year_spinner = StyledSpinner(text=str(datetime.now().year),
                                          values=[str(y) for y in range(2020, 2031)])
        year_box.add_widget(self.year_spinner)
        year_box.add_widget(Label(text="Month:", color=(0, 0, 0, 1)))
        self.month_spinner = StyledSpinner(text=str(datetime.now().month),
                                           values=[str(m) for m in range(1, 13)])
        year_box.add_widget(self.month_spinner)
        layout.add_widget(year_box)
        self.days_grid = GridLayout(cols=7, spacing=dp(5), size_hint_y=None)
        self.days_grid.bind(minimum_height=self.days_grid.setter('height'))
        layout.add_widget(self.days_grid)
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        ok_btn = StyledButton(text="Select", bg_color=Palette.SUCCESS)
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER)
        btn_box.add_widget(ok_btn)
        btn_box.add_widget(cancel_btn)
        layout.add_widget(btn_box)
        self.content = layout
        self.year_spinner.bind(text=self.update_days)
        self.month_spinner.bind(text=self.update_days)
        self.update_days()
        ok_btn.bind(on_release=self.select_date)
        cancel_btn.bind(on_release=self.dismiss)

    def update_days(self, *args):
        self.days_grid.clear_widgets()
        try:
            year = int(self.year_spinner.text)
            month = int(self.month_spinner.text)
        except (ValueError, TypeError):
            return
        num_days = monthrange(year, month)[1]
        for day_name in ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']:
            self.days_grid.add_widget(Label(text=day_name, size_hint_y=None,
                                            height=dp(35), color=(0, 0, 0, 1)))
        for day in range(1, num_days + 1):
            btn = Button(text=str(day), size_hint_y=None, height=dp(35),
                         background_color=(0.9, 0.9, 0.9, 1),
                         color=(0, 0, 0, 1))
            btn.bind(on_press=lambda x, d=day: self.set_selected_day(d))
            self.days_grid.add_widget(btn)
        self.days_grid.height = dp(35) * (1 + (num_days + 6) // 7)

    def set_selected_day(self, day):
        self.selected_day = day

    def select_date(self, instance):
        try:
            date_str = (f"{int(self.year_spinner.text):04d}-"
                        f"{int(self.month_spinner.text):02d}-"
                        f"{self.selected_day:02d}")
            self.on_select(date_str)
        except Exception as e:
            log.warning("Date selection error: %s", e)
        self.dismiss()


# ============================================================
# ==================== ADD TRIP TAB ==========================
# ============================================================
class AddTripTab(ScrollView):
    def __init__(self, app, shift_date, plant_id, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.shift_date = shift_date
        self.plant_id = plant_id
        self.do_scroll_x = False
        self.build_ui()

    def build_ui(self):
        main = BoxLayout(orientation='vertical', spacing=dp(10),
                         padding=dp(12), size_hint_y=None)
        main.bind(minimum_height=main.setter('height'))

        # Customer
        row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        row.add_widget(Label(text="Customer:", size_hint_x=0.3, halign='right',
                             valign='middle', font_size=dp(14), color=(0, 0, 0, 1)))
        self.customer_spinner = StyledSpinner(
            text='Select', values=self.get_customer_names(),
            size_hint_x=0.5, font_size=dp(13))
        row.add_widget(self.customer_spinner)
        btn = StyledButton(text="New", size_hint_x=0.2,
                           bg_color=Palette.PRIMARY_L, font_size=dp(12))
        btn.bind(on_release=self.add_customer)
        row.add_widget(btn)
        main.add_widget(row)

        # Truck
        row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        row.add_widget(Label(text="Truck:", size_hint_x=0.3, halign='right',
                             valign='middle', font_size=dp(14), color=(0, 0, 0, 1)))
        self.truck_input = StyledTextInput(size_hint_x=0.5, font_size=dp(13),
                                            hint_text="Enter truck number")
        row.add_widget(self.truck_input)
        btn = StyledButton(text="Check", size_hint_x=0.2,
                           bg_color=Palette.PRIMARY_L, font_size=dp(12))
        btn.bind(on_release=self.check_truck)
        row.add_widget(btn)
        main.add_widget(row)

        # Driver
        row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        row.add_widget(Label(text="Driver:", size_hint_x=0.3, halign='right',
                             valign='middle', font_size=dp(14), color=(0, 0, 0, 1)))
        self.driver_spinner = StyledSpinner(
            text='Select', values=self.get_driver_names(),
            size_hint_x=0.5, font_size=dp(13))
        row.add_widget(self.driver_spinner)
        btn = StyledButton(text="New", size_hint_x=0.2,
                           bg_color=Palette.PRIMARY_L, font_size=dp(12))
        btn.bind(on_release=self.add_driver)
        row.add_widget(btn)
        main.add_widget(row)

        # Mix
        row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        row.add_widget(Label(text="Mix:", size_hint_x=0.3, halign='right',
                             valign='middle', font_size=dp(14), color=(0, 0, 0, 1)))
        self.mix_spinner = StyledSpinner(
            text='Standard', values=self.app.db.get_mix_types(),
            size_hint_x=0.5, font_size=dp(13))
        row.add_widget(self.mix_spinner)
        btn = StyledButton(text="New", size_hint_x=0.2,
                           bg_color=Palette.SUCCESS, font_size=dp(12))
        btn.bind(on_release=self.add_mix)
        row.add_widget(btn)
        main.add_widget(row)

        # Quantity / Cement
        row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        row.add_widget(Label(text="Qty / Cement (kg/m3):", size_hint_x=0.3,
                             halign='right', valign='middle',
                             font_size=dp(14), color=(0, 0, 0, 1)))
        self.qty_cement_input = StyledTextInput(
            size_hint_x=0.7, font_size=dp(13), hint_text="e.g., 10/425")
        row.add_widget(self.qty_cement_input)
        main.add_widget(row)

        # Silo
        row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        row.add_widget(Label(text="Silo (1-4):", size_hint_x=0.3, halign='right',
                             valign='middle', font_size=dp(14), color=(0, 0, 0, 1)))
        self.silo_input = StyledTextInput(size_hint_x=0.7, font_size=dp(13))
        row.add_widget(self.silo_input)
        main.add_widget(row)

        # Pump
        row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        row.add_widget(Label(text="Pump number:", size_hint_x=0.3, halign='right',
                             valign='middle', font_size=dp(14), color=(0, 0, 0, 1)))
        self.pump_input = StyledTextInput(size_hint_x=0.7, font_size=dp(13))
        row.add_widget(self.pump_input)
        main.add_widget(row)

        add_btn = StyledButton(text="Add Trip", bg_color=Palette.SUCCESS,
                               size_hint_y=None, height=dp(50), font_size=dp(15))
        add_btn.bind(on_release=self.add_trip)
        main.add_widget(add_btn)

        self.message = Label(text="", size_hint_y=None, height=dp(30),
                             color=Palette.SUCCESS, font_size=dp(11))
        main.add_widget(self.message)

        self.add_widget(main)

    def get_customer_names(self):
        return [c['name'] for c in self.app.db.get_customers()] or ['No customers']

    def get_truck_numbers(self):
        return [t['number'] for t in self.app.db.get_trucks()] or ['No trucks']

    def get_driver_names(self):
        return [d['name'] for d in self.app.db.get_drivers()] or ['No drivers']

    def check_truck(self, instance):
        truck_num = self.truck_input.text.strip()
        if not truck_num:
            self.show_alarm("Please enter truck number")
            return
        existing = self.app.db.get_trucks()
        if any(t['number'] == truck_num for t in existing):
            self.message.text = f"Truck {truck_num} exists in database"
            self.message.color = Palette.SUCCESS
        else:
            content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
            content.add_widget(Label(
                text=f"Truck '{truck_num}' not found.\nDo you want to add it?",
                color=(0, 0, 0, 1)))
            btn_box = BoxLayout(size_hint_y=None, height=dp(45))
            yes_btn = StyledButton(text="Yes, Add")
            no_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER)
            btn_box.add_widget(yes_btn); btn_box.add_widget(no_btn)
            content.add_widget(btn_box)
            popup = LightPopup(title="Truck Not Found", content=content,
                                size_hint=(0.8, 0.3))

            def add_new(btn):
                self.app.db.add_truck(truck_num, "Standard")
                self.message.text = f"Truck {truck_num} added"
                popup.dismiss()

            yes_btn.bind(on_release=add_new)
            no_btn.bind(on_release=popup.dismiss)
            popup.open()

    def add_customer(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        name = StyledTextInput(hint_text="Name")
        phone = StyledTextInput(hint_text="Phone")
        addr = StyledTextInput(hint_text="Address")
        save = StyledButton(text="Save", bg_color=Palette.SUCCESS)
        content.add_widget(name); content.add_widget(phone)
        content.add_widget(addr); content.add_widget(save)
        popup = LightPopup(title="New Customer", content=content, size_hint=(0.85, 0.5))

        def save_btn(btn):
            if name.text.strip():
                self.app.db.add_customer(name.text.strip(), phone.text.strip(),
                                          addr.text.strip())
                self.customer_spinner.values = self.get_customer_names()
                self.customer_spinner.text = name.text.strip()
                popup.dismiss()

        save.bind(on_release=save_btn)
        popup.open()

    def add_driver(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        name = StyledTextInput(hint_text="Name")
        phone = StyledTextInput(hint_text="Phone")
        save = StyledButton(text="Save", bg_color=Palette.SUCCESS)
        content.add_widget(name); content.add_widget(phone); content.add_widget(save)
        popup = LightPopup(title="New Driver", content=content, size_hint=(0.85, 0.4))

        def save_btn(btn):
            if name.text.strip():
                self.app.db.add_driver(name.text.strip(), phone.text.strip())
                self.driver_spinner.values = self.get_driver_names()
                self.driver_spinner.text = name.text.strip()
                popup.dismiss()

        save.bind(on_release=save_btn)
        popup.open()

    def add_mix(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        name = StyledTextInput(hint_text="Mix name")
        save = StyledButton(text="Save", bg_color=Palette.SUCCESS)
        content.add_widget(name); content.add_widget(save)
        popup = LightPopup(title="New Mix", content=content, size_hint=(0.85, 0.3))

        def save_btn(btn):
            if name.text.strip() and self.app.db.add_mix_type(name.text.strip()):
                self.mix_spinner.values = self.app.db.get_mix_types()
                self.mix_spinner.text = name.text.strip()
                popup.dismiss()

        save.bind(on_release=save_btn)
        popup.open()

    def show_alarm(self, msg):
        self.message.text = f"! {msg}"
        self.message.color = Palette.DANGER
        Clock.schedule_once(lambda dt: setattr(self.message, 'color', Palette.SUCCESS), 5)
        Clock.schedule_once(lambda dt: setattr(self.message, 'text', ''), 6)

    def update_mix_list(self):
        self.mix_spinner.values = self.app.db.get_mix_types()

    def add_trip(self, instance):
        try:
            if self.customer_spinner.text in ('Select', 'No customers'):
                self.show_alarm("Select customer"); return
            truck_num = self.truck_input.text.strip()
            if not truck_num:
                self.show_alarm("Enter truck number"); return
            if self.driver_spinner.text in ('Select', 'No drivers'):
                self.show_alarm("Select driver"); return

            qc_str = self.qty_cement_input.text.strip()
            if not qc_str or '/' not in qc_str:
                self.show_alarm("Use format: quantity/cement_per_m3 (e.g., 10/425)")
                return
            parts = qc_str.replace(' ', '').split('/')
            if len(parts) != 2:
                self.show_alarm("Invalid format. Use: quantity/cement_per_m3")
                return
            try:
                qty = float(parts[0])
                cement_per_m3 = float(parts[1])
            except ValueError:
                self.show_alarm("Quantity and cement must be numbers")
                return

            if not (MIN_QTY_PER_TRIP <= qty <= MAX_QTY_PER_TRIP):
                self.show_alarm(f"Quantity must be between {MIN_QTY_PER_TRIP} and {MAX_QTY_PER_TRIP} m3")
                return
            if not (MIN_CEMENT_PER_M3 <= cement_per_m3 <= MAX_CEMENT_PER_M3):
                self.show_alarm(f"Cement per m3 must be between {MIN_CEMENT_PER_M3:.0f} and {MAX_CEMENT_PER_M3:.0f} kg")
                return

            try:
                silo = int(self.silo_input.text.strip())
            except ValueError:
                self.show_alarm("Silo must be a number 1-4"); return
            if silo < 1 or silo > 4:
                self.show_alarm("Silo must be 1-4"); return

            cement_total = qty * cement_per_m3
            balances = self.app.db.calculate_balance(self.plant_id, self.shift_date)
            available_in_selected = balances[silo - 1]

            if cement_total <= available_in_selected:
                self._create_single_trip(qty, cement_total, cement_per_m3,
                                          silo, balances)
                return

            used_from_selected = max(available_in_selected, 0)
            remaining_cement = cement_total - used_from_selected
            remaining_qty = remaining_cement / cement_per_m3 if cement_per_m3 > 0 else 0

            if used_from_selected <= 0:
                alternative_silos = [
                    i + 1 for i in range(4)
                    if i + 1 != silo and balances[i] >= cement_total
                ]
                if alternative_silos:
                    self._show_alternative_silos_popup(
                        alternative_silos, balances, qty,
                        cement_total, cement_per_m3)
                    return
                self.show_alarm(f"Insufficient cement in all silos. Required: {cement_total:.0f} kg")
                return

            alternative_silos = [
                i + 1 for i in range(4)
                if i + 1 != silo and balances[i] >= remaining_cement
            ]
            if not alternative_silos:
                self.show_alarm(
                    f"Insufficient cement. Available: {used_from_selected:.0f} kg, "
                    f"still need {remaining_cement:.0f} kg")
                return

            self._show_split_trip_popup(
                silo, used_from_selected, remaining_cement,
                remaining_qty, cement_per_m3,
                alternative_silos, balances, qty)
        except ValueError as e:
            self.show_alarm(f"Invalid numbers: {e}")
        except Exception as e:
            log.error("add_trip error: %s", e)
            log.error(traceback.format_exc())
            self.show_alarm(f"Error: {e}")

    def _create_single_trip(self, qty, cement_total, cement_per_m3, silo, balances):
        comp = self.app.db.get_mix_composition(self.mix_spinner.text)
        shift_start = datetime.strptime(self.shift_date, "%Y-%m-%d")
        shift_start = shift_start.replace(hour=SHIFT_START_HOUR, minute=0, second=0)
        trip_time = datetime.now().strftime("%H:%M")
        trip = self._build_trip_dict(qty, cement_total, cement_per_m3,
                                      silo, comp, shift_start, trip_time)
        self.app.db.add_trip(trip)
        try:
            new_balances = self.app.db.calculate_balance(self.plant_id, self.shift_date)
            self.app.db.save_end_balance(self.plant_id, self.shift_date, new_balances)
        except Exception as e:
            log.warning("Balance refresh after trip failed: %s", e)
        self.app.db.log_action(self.app.current_user, "add_trip",
                                f"Plant {self.plant_id}, Qty: {qty}, Cement: {cement_total:.0f}")
        self._show_success_message(qty, cement_total)
        self._clear_inputs()
        main_screen = self.app.root.get_screen('main')
        main_screen.update_dashboard()

    def _build_trip_dict(self, qty, cement_total, cement_per_m3, silo, comp,
                          shift_start, time_str):
        return {
            'plant_id': self.plant_id,
            'date': shift_start.strftime("%Y-%m-%d"),
            'time': time_str,
            'shift_date': self.shift_date,
            'shift_type': get_shift_type(shift_start),
            'customer': self.customer_spinner.text,
            'truck': self.truck_input.text.strip(),
            'driver': self.driver_spinner.text,
            'mix': self.mix_spinner.text,
            'quantity': qty,
            'cement': cement_total,
            'sand': qty * comp["Sand"],
            'agg1': qty * comp["Agg1"],
            'agg2': qty * comp["Agg2"],
            'water': qty * comp["Water"],
            'add1': qty * comp["Add1"],
            'add2': qty * comp["Add2"],
            'silo': silo,
            'pump': self.pump_input.text.strip(),
            'pump_operator': '',
            'secondary_silo': None,
            'secondary_cement': 0
        }

    def _show_success_message(self, qty, cement_total):
        self.message.text = f"Trip added: {qty:.1f} m3, Cement used: {cement_total:.0f} kg"
        self.message.color = Palette.SUCCESS

    def _clear_inputs(self):
        self.qty_cement_input.text = ""
        self.silo_input.text = ""
        self.pump_input.text = ""

    def _show_alternative_silos_popup(self, silos, balances, qty,
                                       cement_total, cement_per_m3):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        content.add_widget(Label(
            text=(f"Silo {self.silo_input.text} has insufficient cement.\n"
                  f"Required: {cement_total:.0f} kg\n\n"
                  f"Select an alternative silo to use instead:"),
            halign='center', color=(0, 0, 0, 1)))
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        popup = LightPopup(title="Insufficient Cement", content=content, size_hint=(0.8, 0.4))
        for alt_silo in silos:
            btn = StyledButton(text=f"Silo {alt_silo} ({balances[alt_silo-1]:.0f} kg)",
                               bg_color=Palette.INFO)
            btn.bind(on_release=lambda x, s=alt_silo: self._use_alternative_silo(s, popup))
            btn_box.add_widget(btn)
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER)
        cancel_btn.bind(on_release=lambda x: popup.dismiss())
        btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)
        popup.open()

    def _use_alternative_silo(self, new_silo, popup):
        popup.dismiss()
        self.silo_input.text = str(new_silo)
        self.add_trip(None)

    def _show_split_trip_popup(self, original_silo, used_from_selected,
                                remaining_cement, remaining_qty,
                                cement_per_m3, alternative_silos,
                                balances, total_qty):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        content.add_widget(Label(
            text=(f"Silo {original_silo} has only {used_from_selected:.0f} kg.\n"
                  f"Will use all from Silo {original_silo} ({used_from_selected:.0f} kg)\n"
                  f"Remaining needed: {remaining_cement:.0f} kg ({remaining_qty:.2f} m3)\n\n"
                  f"Select silo to supply the remaining:"),
            halign='center', color=(0, 0, 0, 1)))
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        popup = LightPopup(title="Split Trip Required", content=content, size_hint=(0.85, 0.5))
        for alt_silo in alternative_silos:
            btn = StyledButton(text=f"Silo {alt_silo} ({balances[alt_silo-1]:.0f} kg)",
                               bg_color=Palette.INFO)
            btn.bind(on_release=lambda x, s=alt_silo: self._complete_split_trip(
                original_silo, used_from_selected, remaining_cement,
                remaining_qty, cement_per_m3, s, total_qty, popup))
            btn_box.add_widget(btn)
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER)
        cancel_btn.bind(on_release=lambda x: popup.dismiss())
        btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)
        popup.open()

    def _complete_split_trip(self, original_silo, used_cement, remaining_cement,
                              remaining_qty, cement_per_m3, second_silo,
                              total_qty, popup):
        popup.dismiss()
        comp = self.app.db.get_mix_composition(self.mix_spinner.text)
        shift_start = datetime.strptime(self.shift_date, "%Y-%m-%d").replace(
            hour=SHIFT_START_HOUR, minute=0)
        trip_time = datetime.now().strftime("%H:%M")
        trip = self._build_trip_dict(total_qty, used_cement + remaining_cement,
                                      cement_per_m3, original_silo, comp,
                                      shift_start, trip_time)
        trip['cement'] = used_cement
        trip['secondary_silo'] = second_silo
        trip['secondary_cement'] = remaining_cement
        self.app.db.add_trip(trip)
        try:
            balances = self.app.db.calculate_balance(self.plant_id, self.shift_date)
            self.app.db.save_end_balance(self.plant_id, self.shift_date, balances)
        except Exception as e:
            log.warning("Balance refresh after split trip failed: %s", e)
        self.app.db.log_action(
            self.app.current_user, "add_split_trip",
            f"Plant {self.plant_id}, {total_qty:.2f}m3: "
            f"Silo {original_silo}({used_cement:.0f}kg) + Silo {second_silo}({remaining_cement:.0f}kg)")
        self.message.text = (f"Trip added: {total_qty:.2f} m3 "
                             f"(from Silo {original_silo} & {second_silo})")
        self.message.color = Palette.SUCCESS
        self._clear_inputs()
        main_screen = self.app.root.get_screen('main')
        main_screen.update_dashboard()


# ============================================================
# ==================== SEARCH TAB ============================
# ============================================================
class SearchTab(BoxLayout):
    def __init__(self, app, plant_id, **kwargs):
        super().__init__(orientation='vertical', padding=dp(12),
                         spacing=dp(10), **kwargs)
        self.app = app
        self.plant_id = plant_id
        self.last_results = []
        self.build_ui()

    def build_ui(self):
        filter_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        filter_box.add_widget(Label(text="Filter:", size_hint_x=0.3,
                                     color=(0, 0, 0, 1), font_size=dp(13)))
        self.filter_spinner = StyledSpinner(
            text='Customer',
            values=['Customer', 'Truck', 'Driver', 'Mix', 'Pump'],
            size_hint_x=0.7, font_size=dp(13))
        self.filter_spinner.bind(text=self.on_filter_change)
        filter_box.add_widget(self.filter_spinner)
        self.add_widget(filter_box)

        search_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        search_box.add_widget(Label(text="Value:", size_hint_x=0.3,
                                     color=(0, 0, 0, 1), font_size=dp(13)))
        self.search_spinner = StyledSpinner(text='All', values=[],
                                             size_hint_x=0.7, font_size=dp(13))
        search_box.add_widget(self.search_spinner)
        self.add_widget(search_box)

        date_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(8))
        date_box.add_widget(Label(text="From:", size_hint_x=0.1,
                                   color=(0, 0, 0, 1), font_size=dp(12)))
        self.start_date_btn = Button(text="Start Date", size_hint_x=0.4,
                                      background_color=Palette.PRIMARY_L)
        self.start_date_btn.bind(on_press=self.select_start_date)
        date_box.add_widget(self.start_date_btn)
        date_box.add_widget(Label(text="To:", size_hint_x=0.1,
                                   color=(0, 0, 0, 1), font_size=dp(12)))
        self.end_date_btn = Button(text="End Date", size_hint_x=0.4,
                                    background_color=Palette.PRIMARY_L)
        self.end_date_btn.bind(on_press=self.select_end_date)
        date_box.add_widget(self.end_date_btn)
        self.add_widget(date_box)

        self.start_date_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.end_date_str = datetime.now().strftime("%Y-%m-%d")
        self.start_date_btn.text = self.start_date_str
        self.end_date_btn.text = self.end_date_str

        search_btn = StyledButton(text="Search", size_hint_y=None, height=dp(45),
                                  bg_color=Palette.WARNING, font_size=dp(13))
        search_btn.bind(on_release=self.do_search)
        self.add_widget(search_btn)

        self.results_box = BoxLayout(orientation='vertical', size_hint_y=None)
        self.results_box.bind(minimum_height=self.results_box.setter('height'))
        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(self.results_box)
        self.add_widget(scroll)

        pdf_btn = StyledButton(text="Save Search as PDF", size_hint_y=None,
                               height=dp(45), bg_color=Palette.PRIMARY, font_size=dp(13))
        pdf_btn.bind(on_release=self.save_pdf)
        self.add_widget(pdf_btn)

        self.on_filter_change()

    def on_filter_change(self, *args):
        filter_type = self.filter_spinner.text
        values = []
        if filter_type == 'Customer':
            values = [c['name'] for c in self.app.db.get_customers()]
        elif filter_type == 'Truck':
            values = [t['number'] for t in self.app.db.get_trucks()]
        elif filter_type == 'Driver':
            values = [d['name'] for d in self.app.db.get_drivers()]
        elif filter_type == 'Mix':
            values = self.app.db.get_mix_types()
        elif filter_type == 'Pump':
            try:
                c = self.app.db.conn.cursor()
                c.execute(
                    "SELECT DISTINCT pump FROM trips WHERE pump IS NOT NULL AND pump != '' ORDER BY pump")
                values = [row['pump'] for row in c.fetchall()]
            except Exception as e:
                log.warning("pump list failed: %s", e)
                values = []
        values = ['All'] + sorted(set(values))
        self.search_spinner.values = values
        self.search_spinner.text = 'All'

    def select_start_date(self, instance):
        def on_date(date_str):
            self.start_date_str = date_str
            self.start_date_btn.text = date_str
        DatePickerPopup(on_select=on_date).open()

    def select_end_date(self, instance):
        def on_date(date_str):
            self.end_date_str = date_str
            self.end_date_btn.text = date_str
        DatePickerPopup(on_select=on_date).open()

    def do_search(self, instance):
        self.results_box.clear_widgets()
        filter_by = self.filter_spinner.text
        search_value = self.search_spinner.text
        if search_value == 'All':
            search_value = None

        start_dt = f"{self.start_date_str} 00:00"
        end_dt = f"{self.end_date_str} 23:59"

        kwargs = {
            'plant_id': self.plant_id,
            'start_datetime': start_dt,
            'end_datetime': end_dt,
            'limit': None,
        }
        if search_value:
            if filter_by == 'Customer':
                kwargs['customer'] = search_value
            elif filter_by == 'Truck':
                kwargs['truck'] = search_value
            elif filter_by == 'Driver':
                kwargs['driver'] = search_value
            elif filter_by == 'Mix':
                kwargs['mix'] = search_value
            elif filter_by == 'Pump':
                kwargs['pump'] = search_value

        results = self.app.db.get_trips(**kwargs)
        if not results:
            self.add_result("No trips found")
            self.last_results = []
            return

        total_qty = sum(t['quantity'] for t in results)
        total_cem = sum(t['cement'] + (t.get('secondary_cement') or 0)
                        for t in results)
        self.add_result(f"Found {len(results)} trips - "
                        f"Total: {total_qty:.1f} m3, Cement: {total_cem:.0f} kg")
        self.add_result("-" * 50)
        for t in results:
            silo_str = f"Silo {t['silo']}"
            if t.get('secondary_silo'):
                silo_str += f"+{t['secondary_silo']}"
            pump_str = (t.get('pump') or '').strip() or '-'
            self.add_result(
                f"{t['date']} {t['time']} | {t['customer']} | {t['mix']} | "
                f"{t['quantity']:.1f} m3 | Pump {pump_str} | {silo_str}")
        self.last_results = results

    def add_result(self, text):
        lbl = Label(text=text, size_hint_y=None, height=dp(22), halign='left',
                    valign='middle', color=(0, 0, 0, 1), font_size=dp(11))
        lbl.bind(size=lbl.setter('text_size'))
        self.results_box.add_widget(lbl)

    def save_pdf(self, instance):
        if not self.last_results:
            self.add_result("No results")
            return
        progress = ProgressPopup()
        progress.open()

        def generate_pdf(dt):
            try:
                filename = f"search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                filepath = os.path.join(PDF_DIR, filename)
                doc = build_pdf_doc(filepath)
                story = []
                story.append(make_report_title("Search Results"))
                story.append(make_report_subtitle(
                    f"Period: {self.start_date_str} to {self.end_date_str}"))
                story.append(Spacer(1, 12))

                # ✅ استخدام Paragraph لكل خلية لمنع تداخل الأعمدة والنصوص
                header = [
                    P('Date', 9, color=PDF_COLORS['header_text']),
                    P('Time', 9, color=PDF_COLORS['header_text']),
                    P('Customer', 9, color=PDF_COLORS['header_text']),
                    P('Mix', 9, color=PDF_COLORS['header_text']),
                    P('Qty', 9, color=PDF_COLORS['header_text']),
                    P('Truck', 9, color=PDF_COLORS['header_text']),
                    P('Driver', 9, color=PDF_COLORS['header_text']),
                    P('Pump', 9, color=PDF_COLORS['header_text']),
                    P('Silo(s)', 9, color=PDF_COLORS['header_text']),
                ]
                data = [header]
                total = len(self.last_results)
                for i, r in enumerate(self.last_results):
                    silo_str = f"{r['silo']}"
                    if r.get('secondary_silo'):
                        silo_str += f"+{r['secondary_silo']}"
                    pump_str = (r.get('pump') or '').strip() or '-'
                    driver_str = (r.get('driver') or '').strip() or '-'
                    data.append([
                        P(r['date'], 7.5), P(r['time'], 7.5),
                        P(r['customer'], 7.5), P(r['mix'], 7.5),
                        P(f"{r['quantity']:.1f}", 7.5),
                        P(r['truck'], 7.5), P(driver_str, 7.5),
                        P(pump_str, 7.5), P(silo_str, 7.5),
                    ])
                    if i % 20 == 0:
                        progress.update_progress((i / max(total, 1)) * 90,
                                                 f"Processing {i}/{total}")

                total_qty = sum(r['quantity'] for r in self.last_results)
                total_cem = sum(r['cement'] + (r.get('secondary_cement') or 0)
                                for r in self.last_results)
                data.append([
                    P(''), P(''),
                    P('TOTAL', 8, color=PDF_COLORS['header_bg']),
                    P(''), P(f"{total_qty:.1f}", 8, color=PDF_COLORS['header_bg']),
                    P(''), P(''),
                    P(f"{total_cem:.0f}", 8, color=PDF_COLORS['header_bg']),
                    P(''),
                ])

                progress.update_progress(90, "Building table...")
                # ✅ عرض الأعمدة المجموع = 547 (مطابق لعرض A4 مع هوامش 24)
                table = Table(data,
                              colWidths=[58, 38, 118, 72, 42, 40, 92, 42, 45],
                              repeatRows=1)
                style = build_table_style(has_total_row=True)
                add_row_alternating(style, len(data) - 1, start=1)
                table.setStyle(style)
                story.append(table)
                story.extend(make_signature())

                progress.update_progress(95, "Building PDF...")
                doc.build(story)
                notify_media_scanner(filepath)
                progress.dismiss()
                self.add_result(f"PDF saved: {filename}")
                self.show_open_folder_option(filepath)
            except Exception as e:
                log.error("Search PDF error: %s", e)
                log.error(traceback.format_exc())
                try:
                    progress.dismiss()
                except Exception:
                    pass
                self.add_result(f"PDF error: {str(e)}")

        Clock.schedule_once(generate_pdf, 0.1)

    def show_open_folder_option(self, filepath):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        path_label = Label(
            text=f"PDF saved successfully!\n\nLocation:\n{filepath}\n\nDo you want to open the file?",
            halign='center', color=(0, 0, 0, 1), font_size=dp(12))
        path_label.bind(size=path_label.setter('text_size'))
        content.add_widget(path_label)
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        yes_btn = StyledButton(text="Open File", bg_color=Palette.INFO)
        no_btn = StyledButton(text="Close", bg_color=Palette.NEUTRAL)
        btn_box.add_widget(yes_btn); btn_box.add_widget(no_btn)
        content.add_widget(btn_box)
        popup = LightPopup(title="Success", content=content, size_hint=(0.8, 0.4))
        yes_btn.bind(on_release=lambda x: (open_file_location(filepath), popup.dismiss()))
        no_btn.bind(on_release=popup.dismiss)
        popup.open()


# ============================================================
# =================== CEMENT TAB =============================
# ============================================================
class CementTab(BoxLayout):
    def __init__(self, app, shift_date, plant_id, **kwargs):
        super().__init__(orientation='vertical', padding=dp(12),
                         spacing=dp(10), **kwargs)
        self.app = app
        self.shift_date = shift_date
        self.plant_id = plant_id
        self.build_ui()

    def build_ui(self):
        for label, color, handler in (
            ("Add Cement Supply", Palette.INFO, self.add_supply),
            ("Undo Last Supply", Palette.WARNING, self.undo_supply),
            ("Reset Silo Balance (Zero)", Palette.DANGER, self.reset_silo),
            ("Set Start Silo Balance", Palette.PRIMARY, self.set_start_balance),
            ("Show Shift Summary", Palette.SUCCESS, self.show_summary),
            ("Export Shift Summary as PDF", Palette.PRIMARY, self.export_summary_pdf),
        ):
            btn = StyledButton(text=label, bg_color=color,
                               size_hint_y=None, height=dp(45), font_size=dp(13))
            btn.bind(on_release=handler)
            self.add_widget(btn)

        self.message = Label(text="", size_hint_y=0.3, halign='center',
                             valign='top', color=(0, 0, 0, 1), font_size=dp(11))
        self.message.bind(size=self.message.setter('text_size'))
        self.add_widget(self.message)

    def set_start_balance(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        content.add_widget(Label(
            text="Enter starting cement balance for each silo (kg):",
            color=(0, 0, 0, 1), font_size=dp(13)))
        silo_inputs = []
        for i in range(4):
            box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(5))
            box.add_widget(Label(text=f"Silo {i+1}:", size_hint_x=0.3, color=(0, 0, 0, 1)))
            inp = StyledTextInput(text="0", size_hint_x=0.7)
            box.add_widget(inp)
            silo_inputs.append(inp)
            content.add_widget(box)
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        confirm_btn = StyledButton(text="Set Balances", bg_color=Palette.SUCCESS)
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER)
        btn_box.add_widget(confirm_btn); btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)
        popup = LightPopup(title="Set Start Silo Balances",
                            content=content, size_hint=(0.85, 0.6))

        def set_balances(btn):
            try:
                balances = [float(inp.text.strip() or 0) for inp in silo_inputs]
                for i, bal in enumerate(balances):
                    if bal < 0:
                        self.message.text = f"Silo {i+1} balance cannot be negative"
                        return
                    if bal > SILO_CAPACITIES[i + 1]:
                        self.message.text = (f"Silo {i+1} exceeds capacity "
                                             f"({SILO_CAPACITIES[i+1]} kg)")
                        return
                self.app.db.set_initial_balances(self.plant_id, self.shift_date,
                                                  balances)
                self.app.db.log_action(
                    self.app.current_user, "set_start_balance",
                    f"Plant {self.plant_id}, Shift {self.shift_date}: {balances}")
                self.message.text = "Start balances set successfully"
                self.app.root.get_screen('main').update_dashboard()
                popup.dismiss()
            except ValueError:
                self.message.text = "Invalid numbers entered"
            except Exception as e:
                log.error("set_start_balance failed: %s", e)
                self.message.text = f"Error: {e}"

        confirm_btn.bind(on_release=set_balances)
        cancel_btn.bind(on_release=popup.dismiss)
        popup.open()

    def add_supply(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        silo_spinner = StyledSpinner(text='1', values=['1', '2', '3', '4'],
                                      font_size=dp(13))
        amount_input = StyledTextInput(hint_text="Amount (kg)", font_size=dp(13))
        cement_type_input = StyledTextInput(hint_text="Cement Type (e.g., OPC)",
                                             font_size=dp(13))
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        save_btn = StyledButton(text="Add", bg_color=Palette.SUCCESS, font_size=dp(13))
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER, font_size=dp(13))
        btn_box.add_widget(save_btn); btn_box.add_widget(cancel_btn)
        content.add_widget(Label(text="Add Cement to Silo", size_hint_y=None,
                                  height=dp(35), bold=True,
                                  color=(0, 0, 0, 1), font_size=dp(13)))
        content.add_widget(silo_spinner)
        content.add_widget(amount_input)
        content.add_widget(cement_type_input)
        content.add_widget(btn_box)
        popup = LightPopup(title="Cement Supply", content=content, size_hint=(0.85, 0.55))

        def save(btn):
            try:
                silo = int(silo_spinner.text)
                amount = float(amount_input.text.strip())
                if amount <= 0:
                    self.message.text = "Amount must be positive"
                    return
                balances = self.app.db.calculate_balance(self.plant_id, self.shift_date)
                if balances[silo - 1] + amount > SILO_CAPACITIES[silo]:
                    self.message.text = f"Silo {silo} capacity exceeded"
                    return
                shift_start = datetime.strptime(
                    self.shift_date, "%Y-%m-%d").replace(hour=SHIFT_START_HOUR)
                supply = {
                    'plant_id': self.plant_id,
                    'date': shift_start.strftime("%Y-%m-%d"),
                    'time': datetime.now().strftime("%H:%M"),
                    'shift_date': self.shift_date,
                    'silo': silo,
                    'amount': amount,
                    'notes': '',
                    'cement_type': cement_type_input.text.strip(),
                }
                self.app.db.add_cement_supply(supply)
                new_balances = self.app.db.calculate_balance(self.plant_id, self.shift_date)
                self.app.db.save_end_balance(self.plant_id, self.shift_date, new_balances)
                self.app.db.log_action(
                    self.app.current_user, "add_supply",
                    f"Plant {self.plant_id}, Silo {silo}: +{amount} kg")
                self.message.text = f"Added {amount} kg to Silo {silo}"
                self.app.root.get_screen('main').update_dashboard()
                popup.dismiss()
            except ValueError:
                self.message.text = "Invalid input"
            except Exception as e:
                log.error("add_supply failed: %s", e)
                self.message.text = f"Error: {e}"

        save_btn.bind(on_release=save)
        cancel_btn.bind(on_release=popup.dismiss)
        popup.open()

    def undo_supply(self, instance):
        try:
            if self.app.db.undo_last_cement_supply(self.plant_id, self.shift_date):
                self.app.db.recalculate_balances_from_shift(self.plant_id, self.shift_date)
                self.app.db.log_action(self.app.current_user, "undo_supply",
                                        f"Plant {self.plant_id}, Shift: {self.shift_date}")
                self.message.text = "Last supply undone"
                self.app.root.get_screen('main').update_dashboard()
            else:
                self.message.text = "No supplies"
        except Exception as e:
            log.error("undo_supply failed: %s", e)
            self.message.text = f"Error: {e}"

    def reset_silo(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        silo_spinner = StyledSpinner(text='1', values=['1', '2', '3', '4'],
                                      font_size=dp(13))
        content.add_widget(Label(text="Select Silo to Reset to Zero:",
                                 color=(0, 0, 0, 1), font_size=dp(13)))
        content.add_widget(silo_spinner)
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        confirm_btn = StyledButton(text="Reset to Zero", bg_color=Palette.DANGER,
                                    font_size=dp(13))
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.NEUTRAL,
                                   font_size=dp(13))
        btn_box.add_widget(confirm_btn); btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)
        popup2 = LightPopup(title="Reset Silo Balance", content=content,
                             size_hint=(0.8, 0.3))

        def reset(btn):
            try:
                silo = int(silo_spinner.text)
                self.app.db.reset_silo_balance(self.plant_id, silo, 0, self.shift_date)
                self.app.db.log_action(
                    self.app.current_user, "reset_silo",
                    f"Plant {self.plant_id}, Silo {silo} set to 0")
                self.message.text = f"Silo {silo} balance reset to 0"
                self.app.root.get_screen('main').update_dashboard()
                popup2.dismiss()
            except Exception as e:
                log.error("reset_silo failed: %s", e)
                self.message.text = f"Error: {e}"

        confirm_btn.bind(on_release=reset)
        cancel_btn.bind(on_release=popup2.dismiss)
        popup2.open()

    def show_summary(self, instance):
        trips = self.app.db.get_trips(plant_id=self.plant_id, shift_date=self.shift_date)
        supplies = self.app.db.get_cement_supplies(plant_id=self.plant_id,
                                                    shift_date=self.shift_date)
        balances = self.app.db.calculate_balance(self.plant_id, self.shift_date)
        total_conc = sum(t['quantity'] for t in trips)
        total_cem_used = sum(t['cement'] + (t.get('secondary_cement') or 0)
                             for t in trips)
        total_cem_supplied = sum(s['amount'] for s in supplies)
        text = (f"Plant: {self.get_plant_name()}\nShift: {self.shift_date}\n"
                f"Period: {get_shift_range_text(self.shift_date)}\n"
                f"Trips: {len(trips)}\nConcrete: {total_conc:.1f} m3\n"
                f"Cement Used: {total_cem_used:.0f} kg\n"
                f"Cement Supplied: {total_cem_supplied:.0f} kg\n\n")
        pump_summary = self._build_pump_summary(trips)
        if pump_summary:
            text += "Pumps Summary:\n"
            for p, d in sorted(pump_summary.items()):
                text += f"  Pump {p}: {d['volume']:.1f} m3 ({d['trips']} trips)\n"
            text += "\n"
        for i in range(4):
            text += f"Silo {i+1}: {balances[i]:.0f} / {SILO_CAPACITIES[i+1]} kg\n"
        self.message.text = text

    def _build_pump_summary(self, trips):
        summary = {}
        for t in trips:
            pump = (t.get('pump') or '').strip()
            if not pump:
                continue
            summary.setdefault(pump, {'trips': 0, 'volume': 0})
            summary[pump]['trips'] += 1
            summary[pump]['volume'] += t['quantity']
        return summary

    def get_plant_name(self):
        for p in self.app.db.get_plants():
            if p['id'] == self.plant_id:
                return p['name']
        return "Unknown"

    def export_summary_pdf(self, instance):
        try:
            trips = self.app.db.get_trips(plant_id=self.plant_id,
                                           shift_date=self.shift_date)
            supplies = self.app.db.get_cement_supplies(plant_id=self.plant_id,
                                                        shift_date=self.shift_date)
            balances = self.app.db.calculate_balance(self.plant_id, self.shift_date)
            total_conc = sum(t['quantity'] for t in trips)
            total_cem_used = sum(t['cement'] + (t.get('secondary_cement') or 0)
                                 for t in trips)
            total_cem_supplied = sum(s['amount'] for s in supplies)

            mix_summary = {}
            for t in trips:
                mix_summary.setdefault(t['mix'], {'count': 0, 'volume': 0})
                mix_summary[t['mix']]['count'] += 1
                mix_summary[t['mix']]['volume'] += t['quantity']

            customer_summary = {}
            for t in trips:
                cust = t['customer']
                customer_summary.setdefault(cust, {'trips': 0, 'concrete': 0,
                                                    'cement': 0, 'pumps': set()})
                d = customer_summary[cust]
                d['trips'] += 1
                d['concrete'] += t['quantity']
                d['cement'] += t['cement'] + (t.get('secondary_cement') or 0)
                if (t.get('pump') or '').strip():
                    d['pumps'].add(t['pump'].strip())

            pump_summary = self._build_pump_summary(trips)

            prev_balance = (self.app.db.get_shift_start_balance(self.plant_id, self.shift_date)
                            or [0, 0, 0, 0])
            plant_name = self.get_plant_name()
            shift_range = get_shift_range_text(self.shift_date)
        except Exception as e:
            log.error("export_summary_pdf prep failed: %s", e)
            self.message.text = f"Error: {e}"
            return

        progress = ProgressPopup()
        progress.open()

        def generate_pdf(dt):
            try:
                filename = f"shift_summary_{self.plant_id}_{self.shift_date}.pdf"
                filepath = os.path.join(PDF_DIR, filename)
                doc = build_pdf_doc(filepath)
                story = []

                story.append(make_report_title("Shift Production Report"))
                story.append(make_report_subtitle(plant_name))
                story.append(make_report_subtitle(f"Shift: {shift_range}"))
                story.append(Spacer(1, 12))

                story.append(make_section_header("Production Summary"))
                prod_data = [
                    [P('Metric', 9, color=PDF_COLORS['header_text']),
                     P('Value', 9, color=PDF_COLORS['header_text'])],
                    [P('Number of Trips'), P(str(len(trips)))],
                    [P('Total Concrete'), P(f"{total_conc:.1f} m3")],
                    [P('Total Cement Used'), P(f"{total_cem_used:.0f} kg")],
                    [P('Total Cement Supplied'), P(f"{total_cem_supplied:.0f} kg")],
                ]
                prod_table = Table(prod_data, colWidths=[280, 240])
                s = build_table_style()
                add_row_alternating(s, len(prod_data), start=1)
                prod_table.setStyle(s)
                story.append(prod_table)
                story.append(Spacer(1, 14))

                story.append(make_section_header("Mix Types Summary"))
                mix_data = [[
                    P('Mix Type', 9, color=PDF_COLORS['header_text']),
                    P('Trips', 9, color=PDF_COLORS['header_text']),
                    P('Volume (m3)', 9, color=PDF_COLORS['header_text']),
                ]]
                for mix, d in sorted(mix_summary.items()):
                    mix_data.append([P(mix), P(str(d['count'])),
                                     P(f"{d['volume']:.1f}")])
                mix_table = Table(mix_data, colWidths=[280, 110, 130])
                s = build_table_style()
                add_row_alternating(s, len(mix_data), start=1)
                mix_table.setStyle(s)
                story.append(mix_table)
                story.append(Spacer(1, 14))

                progress.update_progress(40, "Building pumps summary...")

                story.append(make_section_header("Pumps Summary (Production per Pump)"))
                if pump_summary:
                    pump_data = [[
                        P('Pump Number', 9, color=PDF_COLORS['header_text']),
                        P('Trips', 9, color=PDF_COLORS['header_text']),
                        P('Concrete Pumped (m3)', 9, color=PDF_COLORS['header_text']),
                    ]]
                    total_pv = 0
                    total_pt = 0
                    for p, d in sorted(pump_summary.items()):
                        pump_data.append([P(f"Pump {p}"), P(str(d['trips'])),
                                          P(f"{d['volume']:.1f}")])
                        total_pv += d['volume']
                        total_pt += d['trips']
                    pump_data.append([P('TOTAL', 8), P(str(total_pt), 8),
                                      P(f"{total_pv:.1f}", 8)])
                    pump_table = Table(pump_data, colWidths=[200, 110, 210])
                    s = build_table_style(has_total_row=True)
                    add_row_alternating(s, len(pump_data) - 1, start=1)
                    pump_table.setStyle(s)
                    story.append(pump_table)
                else:
                    story.append(Paragraph(
                        ar("No pumps assigned to trips in this shift."),
                        ParagraphStyle('NoPump', fontName=ARABIC_FONT_NAME,
                                       fontSize=9, textColor=PDF_COLORS['subtitle'],
                                       alignment=TA_CENTER)))
                story.append(Spacer(1, 14))

                progress.update_progress(60, "Building customers summary...")

                story.append(make_section_header("Customers Summary with Pumps"))
                cust_data = [[
                    P('Customer', 8, color=PDF_COLORS['header_text']),
                    P('Trips', 8, color=PDF_COLORS['header_text']),
                    P('Concrete (m3)', 8, color=PDF_COLORS['header_text']),
                    P('Cement (kg)', 8, color=PDF_COLORS['header_text']),
                    P('Pumps Used', 8, color=PDF_COLORS['header_text']),
                ]]
                for cust, d in sorted(customer_summary.items()):
                    pumps_str = ", ".join(sorted(d['pumps'])) if d['pumps'] else "-"
                    cust_data.append([P(cust, 7.5), P(str(d['trips']), 7.5),
                                      P(f"{d['concrete']:.1f}", 7.5),
                                      P(f"{d['cement']:.0f}", 7.5),
                                      P(pumps_str, 7.5)])
                cust_table = Table(cust_data, colWidths=[150, 50, 90, 90, 140])
                s = build_table_style()
                add_row_alternating(s, len(cust_data), start=1)
                cust_table.setStyle(s)
                story.append(cust_table)
                story.append(Spacer(1, 14))

                progress.update_progress(80, "Building silo balance...")

                story.append(make_section_header("Silo Balance"))
                silo_data = [[
                    P('Silo', 9, color=PDF_COLORS['header_text']),
                    P('Start (kg)', 9, color=PDF_COLORS['header_text']),
                    P('Supplied (kg)', 9, color=PDF_COLORS['header_text']),
                    P('Used (kg)', 9, color=PDF_COLORS['header_text']),
                    P('End (kg)', 9, color=PDF_COLORS['header_text']),
                ]]
                for i in range(4):
                    start = prev_balance[i]
                    supplied = sum(s['amount'] for s in supplies if s['silo'] == i + 1)
                    used = sum((t['cement'] if t['silo'] == i + 1 else 0) +
                               (t['secondary_cement']
                                if t.get('secondary_silo') == i + 1 else 0)
                               for t in trips)
                    end = balances[i]
                    silo_data.append([P(f"Silo {i+1}"), P(f"{start:.0f}"),
                                      P(f"{supplied:.0f}"), P(f"{used:.0f}"),
                                      P(f"{end:.0f}")])
                silo_table = Table(silo_data, colWidths=[110, 100, 110, 100, 100])
                s = build_table_style()
                add_row_alternating(s, len(silo_data), start=1)
                for i, row in enumerate(silo_data[1:], start=1):
                    try:
                        if float(row[4].text) < 0:
                            s.add('TEXTCOLOR', (4, i), (4, i), PDF_COLORS['danger'])
                    except Exception:
                        pass
                silo_table.setStyle(s)
                story.append(silo_table)

                story.extend(make_signature())

                progress.update_progress(95, "Building PDF...")
                doc.build(story)
                notify_media_scanner(filepath)
                progress.dismiss()
                self.message.text = f"PDF saved: {filename}"
                self.show_open_folder_option(filepath)
            except Exception as e:
                log.error("shift summary PDF error: %s", e)
                log.error(traceback.format_exc())
                try:
                    progress.dismiss()
                except Exception:
                    pass
                self.message.text = f"PDF error: {str(e)}"

        Clock.schedule_once(generate_pdf, 0.1)

    def show_open_folder_option(self, filepath):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        path_label = Label(
            text=f"PDF saved successfully!\n\nLocation:\n{filepath}\n\nDo you want to open the file?",
            halign='center', color=(0, 0, 0, 1), font_size=dp(12))
        path_label.bind(size=path_label.setter('text_size'))
        content.add_widget(path_label)
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        yes_btn = StyledButton(text="Open File", bg_color=Palette.INFO)
        no_btn = StyledButton(text="Close", bg_color=Palette.NEUTRAL)
        btn_box.add_widget(yes_btn); btn_box.add_widget(no_btn)
        content.add_widget(btn_box)
        popup = LightPopup(title="Success", content=content, size_hint=(0.8, 0.4))
        yes_btn.bind(on_release=lambda x: (open_file_location(filepath), popup.dismiss()))
        no_btn.bind(on_release=popup.dismiss)
        popup.open()


# ============================================================
# ==================== MIX TYPES TAB =========================
# ============================================================
class MixTypesTab(ScrollView):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.do_scroll_x = False
        self.build_ui()

    def build_ui(self):
        main = BoxLayout(orientation='vertical', spacing=dp(10),
                         padding=dp(12), size_hint_y=None)
        main.bind(minimum_height=main.setter('height'))

        self.mix_spinner = StyledSpinner(text='Select Mix',
                                          values=self.app.db.get_mix_types(),
                                          size_hint_y=None, height=dp(45),
                                          font_size=dp(13))
        main.add_widget(self.mix_spinner)

        self.comp_inputs = {}
        for label in ('Sand (kg/m3)', 'Agg1 (kg/m3)', 'Agg2 (kg/m3)',
                      'Water (kg/m3)', 'Add1 (kg/m3)', 'Add2 (kg/m3)'):
            row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
            row.add_widget(Label(text=label, size_hint_x=0.4,
                                 color=(0, 0, 0, 1), font_size=dp(13)))
            inp = StyledTextInput(text="0", size_hint_x=0.6, font_size=dp(13))
            self.comp_inputs[label] = inp
            row.add_widget(inp)
            main.add_widget(row)

        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        save_btn = StyledButton(text="Save Changes", bg_color=Palette.SUCCESS,
                                font_size=dp(13))
        save_btn.bind(on_release=self.save_composition)
        add_btn = StyledButton(text="Add New Mix", bg_color=Palette.PRIMARY_L,
                               font_size=dp(13))
        add_btn.bind(on_release=self.add_new_mix)
        btn_box.add_widget(save_btn)
        btn_box.add_widget(add_btn)
        main.add_widget(btn_box)

        clear_btn = StyledButton(text="Delete All Custom Mixes",
                                  bg_color=Palette.DANGER, font_size=dp(13))
        clear_btn.bind(on_release=self.clear_custom_mixes)
        main.add_widget(clear_btn)

        self.message = Label(text="", size_hint_y=None, height=dp(30),
                             color=Palette.SUCCESS, font_size=dp(11))
        main.add_widget(self.message)

        self.mix_spinner.bind(text=self.load_composition)
        self.load_composition()
        self.add_widget(main)

    def load_composition(self, *args):
        mix = self.mix_spinner.text
        if mix != 'Select Mix':
            comp = self.app.db.get_mix_composition(mix)
            self.comp_inputs['Sand (kg/m3)'].text = str(comp['Sand'])
            self.comp_inputs['Agg1 (kg/m3)'].text = str(comp['Agg1'])
            self.comp_inputs['Agg2 (kg/m3)'].text = str(comp['Agg2'])
            self.comp_inputs['Water (kg/m3)'].text = str(comp['Water'])
            self.comp_inputs['Add1 (kg/m3)'].text = str(comp['Add1'])
            self.comp_inputs['Add2 (kg/m3)'].text = str(comp['Add2'])

    def save_composition(self, instance):
        mix = self.mix_spinner.text
        if mix == 'Select Mix':
            self.message.text = "Select a mix first"
            self.message.color = Palette.DANGER
            Clock.schedule_once(
                lambda dt: setattr(self.message, 'color', Palette.SUCCESS), 3)
            return
        try:
            comp = {
                'Sand': float(self.comp_inputs['Sand (kg/m3)'].text or 0),
                'Agg1': float(self.comp_inputs['Agg1 (kg/m3)'].text or 0),
                'Agg2': float(self.comp_inputs['Agg2 (kg/m3)'].text or 0),
                'Water': float(self.comp_inputs['Water (kg/m3)'].text or 0),
                'Add1': float(self.comp_inputs['Add1 (kg/m3)'].text or 0),
                'Add2': float(self.comp_inputs['Add2 (kg/m3)'].text or 0),
            }
            self.app.db.update_mix_composition(mix, comp)
            self.app.db.log_action(self.app.current_user, "edit_mix", f"Mix: {mix}")
            self.message.text = f"Composition for {mix} saved"
            self.message.color = Palette.SUCCESS
            Clock.schedule_once(lambda dt: setattr(self.message, 'text', ''), 3)
        except ValueError:
            self.message.text = "Invalid numbers"
            self.message.color = Palette.DANGER

    def add_new_mix(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(15))
        content.add_widget(Label(text="New Mix Name:", size_hint_y=None,
                                  height=dp(30), color=(0, 0, 0, 1), font_size=dp(13)))
        name_input = StyledTextInput(hint_text="e.g. Custom Mix", font_size=dp(13))
        content.add_widget(name_input)

        comp_fields = {}
        for label in ('Sand (kg/m3)', 'Agg1 (kg/m3)', 'Agg2 (kg/m3)',
                      'Water (kg/m3)', 'Add1 (kg/m3)', 'Add2 (kg/m3)'):
            row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
            row.add_widget(Label(text=label, size_hint_x=0.4,
                                 color=(0, 0, 0, 1), font_size=dp(13)))
            inp = ModernInput(font_size=dp(13))
            row.add_widget(inp)
            comp_fields[label] = inp
            content.add_widget(row)

        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        save_btn = StyledButton(text="Add Mix", bg_color=Palette.SUCCESS, font_size=dp(13))
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER, font_size=dp(13))
        btn_box.add_widget(save_btn); btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)

        popup = LightPopup(title="Add New Mix Type", content=content,
                            size_hint=(0.85, 0.7))

        def save_new(btn):
            name = name_input.text.strip()
            if not name:
                self.message.text = "Please enter mix name"
                self.message.color = Palette.DANGER
                return
            try:
                vals = {k: float(v.text or 0) for k, v in comp_fields.items()}
                if self.app.db.add_custom_mix(
                        name, vals['Sand (kg/m3)'], vals['Agg1 (kg/m3)'],
                        vals['Agg2 (kg/m3)'], vals['Water (kg/m3)'],
                        vals['Add1 (kg/m3)'], vals['Add2 (kg/m3)']):
                    self.mix_spinner.values = self.app.db.get_mix_types()
                    self.mix_spinner.text = name
                    main_screen = self.app.root.get_screen('main')
                    add_trip = main_screen.contents.get('Add Trip')
                    if add_trip:
                        add_trip.update_mix_list()
                    self.app.db.log_action(self.app.current_user, "add_mix",
                                            f"Mix: {name}")
                    self.message.text = f"Mix '{name}' added successfully"
                    self.message.color = Palette.SUCCESS
                    popup.dismiss()
                    Clock.schedule_once(
                        lambda dt: setattr(self.message, 'text', ''), 3)
                else:
                    self.message.text = "Mix name already exists"
                    self.message.color = Palette.DANGER
            except ValueError:
                self.message.text = "Invalid numbers"
                self.message.color = Palette.DANGER
            except Exception as e:
                log.error("add_new_mix failed: %s", e)
                self.message.text = f"Error: {e}"

        save_btn.bind(on_release=save_new)
        cancel_btn.bind(on_release=popup.dismiss)
        popup.open()

    def clear_custom_mixes(self, instance):
        try:
            deleted = self.app.db.delete_all_custom_mixes()
            self.mix_spinner.values = self.app.db.get_mix_types()
            self.mix_spinner.text = 'Standard'
            self.load_composition()
            main_screen = self.app.root.get_screen('main')
            add_trip = main_screen.contents.get('Add Trip')
            if add_trip:
                add_trip.update_mix_list()
            self.app.db.log_action(self.app.current_user, "clear_mixes",
                                    f"Deleted {deleted} mixes")
            self.message.text = (f"Deleted {deleted} custom mix(es). "
                                 f"Only 'Standard' remains.")
            self.message.color = Palette.SUCCESS
            Clock.schedule_once(lambda dt: setattr(self.message, 'text', ''), 3)
        except Exception as e:
            log.error("clear_custom_mixes failed: %s", e)
            self.message.text = f"Error: {e}"


# ============================================================
# =================== REPORTS TAB ============================
# ============================================================
class ReportsTab(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation='vertical', padding=dp(12),
                         spacing=dp(10), **kwargs)
        self.app = app
        self.last_settings = {'start_date': None, 'start_time': '08:00',
                              'end_date': None, 'end_time': '08:00',
                              'plant_id': None}
        self.last_trips = []
        self.last_start = ""
        self.last_end = ""
        self.last_plant_name = ""
        self.build_ui()
        self.load_last_settings()

    def build_ui(self):
        plant_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        plant_box.add_widget(Label(text="Plant:", color=(0, 0, 0, 1),
                                    size_hint_x=0.15, font_size=dp(13)))
        self.plant_spinner = StyledSpinner(text='All Plants',
                                            values=self.get_plant_options(),
                                            size_hint_x=0.85, font_size=dp(13))
        plant_box.add_widget(self.plant_spinner)
        self.add_widget(plant_box)

        self.start_date_str = datetime.now().strftime("%Y-%m-%d")
        self.end_date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        start_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        start_box.add_widget(Label(text="Start:", color=(0, 0, 0, 1),
                                    size_hint_x=0.15, font_size=dp(13)))
        self.start_date_btn = Button(text=self.start_date_str, size_hint_x=0.35,
                                      background_color=Palette.PRIMARY_L)
        self.start_date_btn.bind(on_press=self.select_start_date)
        self.start_time_spinner = Spinner(
            text='08:00',
            values=[f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)],
            size_hint_x=0.35, background_color=(1, 1, 1, 1),
            color=(0, 0, 0, 1), font_size=dp(11))
        start_box.add_widget(self.start_date_btn)
        start_box.add_widget(self.start_time_spinner)
        self.add_widget(start_box)

        end_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        end_box.add_widget(Label(text="End:", color=(0, 0, 0, 1),
                                  size_hint_x=0.15, font_size=dp(13)))
        self.end_date_btn = Button(text=self.end_date_str, size_hint_x=0.35,
                                    background_color=Palette.PRIMARY_L)
        self.end_date_btn.bind(on_press=self.select_end_date)
        self.end_time_spinner = Spinner(
            text='08:00',
            values=[f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)],
            size_hint_x=0.35, background_color=(1, 1, 1, 1),
            color=(0, 0, 0, 1), font_size=dp(11))
        end_box.add_widget(self.end_date_btn)
        end_box.add_widget(self.end_time_spinner)
        self.add_widget(end_box)

        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        show_btn = StyledButton(text="Show Report", bg_color=Palette.INFO,
                                font_size=dp(13))
        show_btn.bind(on_release=self.show_period_report)
        pdf_btn = StyledButton(text="Export PDF", bg_color=Palette.PRIMARY,
                               font_size=dp(13))
        pdf_btn.bind(on_release=self.export_period_pdf)
        btn_box.add_widget(show_btn)
        btn_box.add_widget(pdf_btn)
        self.add_widget(btn_box)

        self.report_text = Label(text="", size_hint_y=0.6, halign='left',
                                 valign='top', color=(0, 0, 0, 1), font_size=dp(11))
        self.report_text.bind(size=self.report_text.setter('text_size'))
        self.add_widget(self.report_text)

    def get_plant_options(self):
        return ['All Plants'] + [p['name'] for p in self.app.db.get_plants()]

    def get_selected_plant_id(self):
        selected = self.plant_spinner.text
        if selected == 'All Plants':
            return None
        for p in self.app.db.get_plants():
            if p['name'] == selected:
                return p['id']
        return None

    def load_last_settings(self):
        if self.last_settings['start_date']:
            self.start_date_btn.text = self.last_settings['start_date']
            self.start_date_str = self.last_settings['start_date']
        if self.last_settings['end_date']:
            self.end_date_btn.text = self.last_settings['end_date']
            self.end_date_str = self.last_settings['end_date']
        self.start_time_spinner.text = self.last_settings['start_time']
        self.end_time_spinner.text = self.last_settings['end_time']
        if self.last_settings['plant_id'] is not None:
            for p in self.app.db.get_plants():
                if p['id'] == self.last_settings['plant_id']:
                    self.plant_spinner.text = p['name']
                    break

    def save_current_settings(self):
        self.last_settings = {
            'start_date': self.start_date_str,
            'start_time': self.start_time_spinner.text,
            'end_date': self.end_date_str,
            'end_time': self.end_time_spinner.text,
            'plant_id': self.get_selected_plant_id(),
        }

    def select_start_date(self, instance):
        def on_date(date_str):
            self.start_date_str = date_str
            self.start_date_btn.text = date_str
            self.save_current_settings()
        DatePickerPopup(on_select=on_date).open()

    def select_end_date(self, instance):
        def on_date(date_str):
            self.end_date_str = date_str
            self.end_date_btn.text = date_str
            self.save_current_settings()
        DatePickerPopup(on_select=on_date).open()

    def get_period_trips(self):
        start_datetime = f"{self.start_date_str} {self.start_time_spinner.text}"
        end_datetime = f"{self.end_date_str} {self.end_time_spinner.text}"
        return self.app.db.get_trips(plant_id=self.get_selected_plant_id(),
                                      start_datetime=start_datetime,
                                      end_datetime=end_datetime, limit=None)

    def show_period_report(self, instance):
        trips = self.get_period_trips()
        start_dt = f"{self.start_date_str} {self.start_time_spinner.text}"
        end_dt = f"{self.end_date_str} {self.end_time_spinner.text}"
        plant_name = self.plant_spinner.text
        total_conc = sum(t['quantity'] for t in trips)
        total_cem = sum(t['cement'] + (t.get('secondary_cement') or 0) for t in trips)
        text = (f"Period Report - {plant_name}\nFrom: {start_dt}\nTo: {end_dt}\n"
                f"Total Trips: {len(trips)}\n"
                f"Total Concrete: {total_conc:.1f} m3\n"
                f"Total Cement: {total_cem:.0f} kg\n\n")

        pump_summary = {}
        for t in trips:
            pump = (t.get('pump') or '').strip()
            if not pump:
                continue
            pump_summary.setdefault(pump, {'trips': 0, 'volume': 0})
            pump_summary[pump]['trips'] += 1
            pump_summary[pump]['volume'] += t['quantity']
        if pump_summary:
            text += "Pumps Summary:\n"
            for p, d in sorted(pump_summary.items()):
                text += f"  Pump {p}: {d['volume']:.1f} m3 ({d['trips']} trips)\n"
            text += "\n"

        if trips:
            text += "Trip Details:\n"
            for t in trips[:30]:
                silo_str = f"Silo {t['silo']}"
                if t.get('secondary_silo'):
                    silo_str += f"+{t['secondary_silo']}"
                pump_str = (t.get('pump') or '').strip() or '-'
                text += (f"{t['date']} {t['time']} | {t['customer']} | "
                         f"{t['mix']} | {t['quantity']:.1f} m3 | "
                         f"Pump {pump_str} | {silo_str}\n")
        self.report_text.text = text
        self.last_trips = trips
        self.last_start = start_dt
        self.last_end = end_dt
        self.last_plant_name = plant_name

    def export_period_pdf(self, instance):
        if not self.last_trips:
            self.report_text.text = "No data to export. Please show report first."
            return
        self.save_current_settings()
        progress = ProgressPopup()
        progress.open()

        def generate_pdf(dt):
            try:
                filename = f"period_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                filepath = os.path.join(PDF_DIR, filename)
                doc = build_pdf_doc(filepath)
                story = []

                story.append(make_report_title(
                    f"Period Production Report - {self.last_plant_name}"))
                story.append(make_report_subtitle(f"From: {self.last_start}"))
                story.append(make_report_subtitle(f"To: {self.last_end}"))
                story.append(Spacer(1, 12))

                total_conc = sum(t['quantity'] for t in self.last_trips)
                total_cem = sum(t['cement'] + (t.get('secondary_cement') or 0)
                                for t in self.last_trips)

                story.append(make_section_header("Summary"))
                summary_data = [
                    [P('Metric', 9, color=PDF_COLORS['header_text']),
                     P('Value', 9, color=PDF_COLORS['header_text'])],
                    [P('Total Trips'), P(str(len(self.last_trips)))],
                    [P('Total Concrete'), P(f"{total_conc:.1f} m3")],
                    [P('Total Cement'), P(f"{total_cem:.0f} kg")],
                ]
                smt = Table(summary_data, colWidths=[280, 240])
                s = build_table_style()
                add_row_alternating(s, len(summary_data), start=1)
                smt.setStyle(s)
                story.append(smt)
                story.append(Spacer(1, 12))

                pump_summary = {}
                for t in self.last_trips:
                    pump = (t.get('pump') or '').strip()
                    if not pump:
                        continue
                    pump_summary.setdefault(pump, {'trips': 0, 'volume': 0})
                    pump_summary[pump]['trips'] += 1
                    pump_summary[pump]['volume'] += t['quantity']
                if pump_summary:
                    story.append(make_section_header(
                        "Pumps Summary (Production per Pump)"))
                    pump_data = [[
                        P('Pump Number', 9, color=PDF_COLORS['header_text']),
                        P('Trips', 9, color=PDF_COLORS['header_text']),
                        P('Concrete Pumped (m3)', 9, color=PDF_COLORS['header_text']),
                    ]]
                    total_pv = 0
                    total_pt = 0
                    for p, d in sorted(pump_summary.items()):
                        pump_data.append([P(f"Pump {p}"), P(str(d['trips'])),
                                          P(f"{d['volume']:.1f}")])
                        total_pv += d['volume']
                        total_pt += d['trips']
                    pump_data.append([P('TOTAL', 8), P(str(total_pt), 8),
                                      P(f"{total_pv:.1f}", 8)])
                    pump_table = Table(pump_data, colWidths=[200, 110, 210])
                    s = build_table_style(has_total_row=True)
                    add_row_alternating(s, len(pump_data) - 1, start=1)
                    pump_table.setStyle(s)
                    story.append(pump_table)
                    story.append(Spacer(1, 12))

                progress.update_progress(50, "Building trip details...")

                if self.last_trips:
                    story.append(make_section_header("Trip Details"))
                    header = [
                        P('Date', 9, color=PDF_COLORS['header_text']),
                        P('Time', 9, color=PDF_COLORS['header_text']),
                        P('Customer', 9, color=PDF_COLORS['header_text']),
                        P('Mix', 9, color=PDF_COLORS['header_text']),
                        P('Qty', 9, color=PDF_COLORS['header_text']),
                        P('Truck', 9, color=PDF_COLORS['header_text']),
                        P('Driver', 9, color=PDF_COLORS['header_text']),
                        P('Pump', 9, color=PDF_COLORS['header_text']),
                        P('Silo(s)', 9, color=PDF_COLORS['header_text']),
                    ]
                    data = [header[:]]
                    rows_per_page = 30
                    total = len(self.last_trips)
                    for i, t in enumerate(self.last_trips):
                        silo_str = f"{t['silo']}"
                        if t.get('secondary_silo'):
                            silo_str += f"+{t['secondary_silo']}"
                        pump_str = (t.get('pump') or '').strip() or '-'
                        driver_str = (t.get('driver') or '').strip() or '-'
                        data.append([
                            P(t['date'], 7.5), P(t['time'], 7.5),
                            P(t['customer'], 7.5), P(t['mix'], 7.5),
                            P(f"{t['quantity']:.1f}", 7.5),
                            P(t['truck'], 7.5), P(driver_str, 7.5),
                            P(pump_str, 7.5), P(silo_str, 7.5),
                        ])
                        if (len(data) - 1) % rows_per_page == 0 or i == total - 1:
                            tbl = Table(data,
                                        colWidths=[58, 38, 118, 72, 42, 40, 92, 42, 45],
                                        repeatRows=1)
                            s = build_table_style()
                            add_row_alternating(s, len(data), start=1)
                            tbl.setStyle(s)
                            story.append(tbl)
                            if i < total - 1:
                                story.append(PageBreak())
                            data = [header[:]]
                            progress.update_progress(
                                10 + (i / max(total, 1)) * 80,
                                f"Processing {i+1}/{total}")

                story.extend(make_signature())
                progress.update_progress(95, "Building PDF...")
                doc.build(story)
                notify_media_scanner(filepath)
                progress.dismiss()
                self.report_text.text = f"PDF saved: {filename}"
                self.show_open_folder_option(filepath)
            except Exception as e:
                log.error("Period PDF error: %s", e)
                log.error(traceback.format_exc())
                try:
                    progress.dismiss()
                except Exception:
                    pass
                self.report_text.text = f"PDF error: {str(e)}"

        Clock.schedule_once(generate_pdf, 0.1)

    def show_open_folder_option(self, filepath):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        path_label = Label(
            text=f"PDF saved successfully!\n\nLocation:\n{filepath}\n\nDo you want to open the file?",
            halign='center', color=(0, 0, 0, 1), font_size=dp(12))
        path_label.bind(size=path_label.setter('text_size'))
        content.add_widget(path_label)
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        yes_btn = StyledButton(text="Open File", bg_color=Palette.INFO)
        no_btn = StyledButton(text="Close", bg_color=Palette.NEUTRAL)
        btn_box.add_widget(yes_btn); btn_box.add_widget(no_btn)
        content.add_widget(btn_box)
        popup = LightPopup(title="Success", content=content, size_hint=(0.8, 0.4))
        yes_btn.bind(on_release=lambda x: (open_file_location(filepath), popup.dismiss()))
        no_btn.bind(on_release=popup.dismiss)
        popup.open()


# ============================================================
# ============= CUSTOMER CONSUMPTION TAB =====================
# ============================================================
class CustomerConsumptionTab(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation='vertical', padding=dp(12),
                         spacing=dp(10), **kwargs)
        self.app = app
        self.last_data = []
        self.last_start = ""
        self.last_end = ""
        self.last_plant_name = ""
        self.last_settings = {'start_date': None, 'start_time': '08:00',
                              'end_date': None, 'end_time': '08:00',
                              'customer': 'All Customers', 'plant_id': None}
        self.build_ui()
        self.load_last_settings()

    def build_ui(self):
        plant_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        plant_box.add_widget(Label(text="Plant:", color=(0, 0, 0, 1),
                                    size_hint_x=0.2, font_size=dp(13)))
        self.plant_spinner = StyledSpinner(text='All Plants',
                                            values=self.get_plant_options(),
                                            size_hint_x=0.8, font_size=dp(13))
        plant_box.add_widget(self.plant_spinner)
        self.add_widget(plant_box)

        cust_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        cust_box.add_widget(Label(text="Customer:", color=(0, 0, 0, 1),
                                   size_hint_x=0.2, font_size=dp(13)))
        self.customer_spinner = StyledSpinner(
            text='All Customers',
            values=['All Customers'] + self.get_customer_names(),
            size_hint_x=0.8, font_size=dp(13))
        cust_box.add_widget(self.customer_spinner)
        self.add_widget(cust_box)

        self.start_date_str = datetime.now().strftime("%Y-%m-%d")
        self.end_date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        start_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        start_box.add_widget(Label(text="Start:", color=(0, 0, 0, 1),
                                    size_hint_x=0.15, font_size=dp(13)))
        self.start_date_btn = Button(text=self.start_date_str, size_hint_x=0.35,
                                      background_color=Palette.PRIMARY_L)
        self.start_date_btn.bind(on_press=self.select_start_date)
        self.start_time_spinner = Spinner(
            text='08:00',
            values=[f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)],
            size_hint_x=0.35, background_color=(1, 1, 1, 1),
            color=(0, 0, 0, 1), font_size=dp(11))
        start_box.add_widget(self.start_date_btn)
        start_box.add_widget(self.start_time_spinner)
        self.add_widget(start_box)

        end_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        end_box.add_widget(Label(text="End:", color=(0, 0, 0, 1),
                                  size_hint_x=0.15, font_size=dp(13)))
        self.end_date_btn = Button(text=self.end_date_str, size_hint_x=0.35,
                                    background_color=Palette.PRIMARY_L)
        self.end_date_btn.bind(on_press=self.select_end_date)
        self.end_time_spinner = Spinner(
            text='08:00',
            values=[f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)],
            size_hint_x=0.35, background_color=(1, 1, 1, 1),
            color=(0, 0, 0, 1), font_size=dp(11))
        end_box.add_widget(self.end_date_btn)
        end_box.add_widget(self.end_time_spinner)
        self.add_widget(end_box)

        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        show_btn = StyledButton(text="Show Consumption", bg_color=Palette.INFO,
                                font_size=dp(13))
        show_btn.bind(on_release=self.show_consumption)
        pdf_btn = StyledButton(text="Export PDF", bg_color=Palette.PRIMARY,
                               font_size=dp(13))
        pdf_btn.bind(on_release=self.export_pdf)
        btn_box.add_widget(show_btn)
        btn_box.add_widget(pdf_btn)
        self.add_widget(btn_box)

        self.report_text = Label(text="", size_hint_y=0.6, halign='left',
                                 valign='top', color=(0, 0, 0, 1), font_size=dp(11))
        self.report_text.bind(size=self.report_text.setter('text_size'))
        self.add_widget(self.report_text)

    def get_plant_options(self):
        return ['All Plants'] + [p['name'] for p in self.app.db.get_plants()]

    def get_selected_plant_id(self):
        selected = self.plant_spinner.text
        if selected == 'All Plants':
            return None
        for p in self.app.db.get_plants():
            if p['name'] == selected:
                return p['id']
        return None

    def get_customer_names(self):
        return [c['name'] for c in self.app.db.get_customers()]

    def load_last_settings(self):
        if self.last_settings['start_date']:
            self.start_date_btn.text = self.last_settings['start_date']
            self.start_date_str = self.last_settings['start_date']
        if self.last_settings['end_date']:
            self.end_date_btn.text = self.last_settings['end_date']
            self.end_date_str = self.last_settings['end_date']
        self.start_time_spinner.text = self.last_settings['start_time']
        self.end_time_spinner.text = self.last_settings['end_time']
        self.customer_spinner.text = self.last_settings['customer']
        if self.last_settings['plant_id'] is not None:
            for p in self.app.db.get_plants():
                if p['id'] == self.last_settings['plant_id']:
                    self.plant_spinner.text = p['name']
                    break

    def save_current_settings(self):
        self.last_settings = {
            'start_date': self.start_date_str,
            'start_time': self.start_time_spinner.text,
            'end_date': self.end_date_str,
            'end_time': self.end_time_spinner.text,
            'customer': self.customer_spinner.text,
            'plant_id': self.get_selected_plant_id(),
        }

    def select_start_date(self, instance):
        def on_date(date_str):
            self.start_date_str = date_str
            self.start_date_btn.text = date_str
            self.save_current_settings()
        DatePickerPopup(on_select=on_date).open()

    def select_end_date(self, instance):
        def on_date(date_str):
            self.end_date_str = date_str
            self.end_date_btn.text = date_str
            self.save_current_settings()
        DatePickerPopup(on_select=on_date).open()

    def show_consumption(self, instance):
        self.save_current_settings()
        start_dt = f"{self.start_date_str} {self.start_time_spinner.text}"
        end_dt = f"{self.end_date_str} {self.end_time_spinner.text}"
        plant_id = self.get_selected_plant_id()
        customer = (self.customer_spinner.text
                    if self.customer_spinner.text != 'All Customers' else None)

        data = self.app.db.get_customer_consumption_period(start_dt, end_dt,
                                                           plant_id, customer)
        if not data:
            self.report_text.text = "No consumption data found for this period."
            self.last_data = []
            self.last_start = start_dt
            self.last_end = end_dt
            self.last_plant_name = self.plant_spinner.text
            return

        totals = {k: sum(d[k] for d in data) for k in
                  ('total_concrete', 'total_cement', 'total_sand', 'total_agg1',
                   'total_agg2', 'total_water', 'total_add1', 'total_add2')}
        text = (f"Consumption Report - {self.plant_spinner.text}\n"
                f"From: {start_dt}\nTo: {end_dt}\n")
        if customer:
            text += f"Customer: {customer}\n"
        text += (f"Total Trips: {sum(d['trip_count'] for d in data)}\n"
                 f"Total Concrete: {totals['total_concrete']:.1f} m3\n"
                 f"Total Cement: {totals['total_cement']:.0f} kg\n"
                 f"Total Sand: {totals['total_sand']:.0f} kg\n"
                 f"Total Agg1: {totals['total_agg1']:.0f} kg\n"
                 f"Total Agg2: {totals['total_agg2']:.0f} kg\n"
                 f"Total Water: {totals['total_water']:.0f} kg\n"
                 f"Total Add1: {totals['total_add1']:.0f} kg\n"
                 f"Total Add2: {totals['total_add2']:.0f} kg\n\n")

        if not customer:
            text += "Breakdown by Customer:\n"
            for d in data:
                text += (f"\n{d['customer']}: {d['total_concrete']:.1f} m3 "
                         f"({d['trip_count']} trips)\n"
                         f"  Cement: {d['total_cement']:.0f} kg\n"
                         f"  Sand: {d['total_sand']:.0f} kg\n"
                         f"  Agg1: {d['total_agg1']:.0f} kg\n"
                         f"  Agg2: {d['total_agg2']:.0f} kg\n"
                         f"  Water: {d['total_water']:.0f} kg\n"
                         f"  Add1: {d['total_add1']:.0f} kg\n"
                         f"  Add2: {d['total_add2']:.0f} kg\n")

        self.report_text.text = text
        self.last_data = data
        self.last_start = start_dt
        self.last_end = end_dt
        self.last_plant_name = self.plant_spinner.text

    def export_pdf(self, instance):
        if not self.last_data:
            self.report_text.text = "No data to export. Please show consumption first."
            return
        self.save_current_settings()
        progress = ProgressPopup()
        progress.open()

        def generate_pdf(dt):
            try:
                filename = f"consumption_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                filepath = os.path.join(PDF_DIR, filename)
                doc = build_pdf_doc(filepath)
                story = []

                story.append(make_report_title(
                    f"Customer Consumption Report - {self.last_plant_name}"))
                story.append(make_report_subtitle(f"From: {self.last_start}"))
                story.append(make_report_subtitle(f"To: {self.last_end}"))
                if self.customer_spinner.text != 'All Customers':
                    story.append(make_report_subtitle(
                        f"Customer: {self.customer_spinner.text}"))
                story.append(Spacer(1, 12))

                totals = {
                    'cement': sum(d['total_cement'] for d in self.last_data),
                    'sand': sum(d['total_sand'] for d in self.last_data),
                    'agg1': sum(d['total_agg1'] for d in self.last_data),
                    'agg2': sum(d['total_agg2'] for d in self.last_data),
                    'water': sum(d['total_water'] for d in self.last_data),
                    'add1': sum(d['total_add1'] for d in self.last_data),
                    'add2': sum(d['total_add2'] for d in self.last_data),
                }

                story.append(make_section_header("Summary Totals"))
                summary_data = [
                    [P('Material', 9, color=PDF_COLORS['header_text']),
                     P('Total (kg)', 9, color=PDF_COLORS['header_text'])],
                    [P('Cement'), P(f"{totals['cement']:.0f}")],
                    [P('Sand'), P(f"{totals['sand']:.0f}")],
                    [P('Aggregate 1'), P(f"{totals['agg1']:.0f}")],
                    [P('Aggregate 2'), P(f"{totals['agg2']:.0f}")],
                    [P('Water'), P(f"{totals['water']:.0f}")],
                    [P('Additive 1'), P(f"{totals['add1']:.0f}")],
                    [P('Additive 2'), P(f"{totals['add2']:.0f}")],
                ]
                summary_table = Table(summary_data, colWidths=[280, 240])
                s = build_table_style()
                add_row_alternating(s, len(summary_data), start=1)
                summary_table.setStyle(s)
                story.append(summary_table)
                story.append(Spacer(1, 14))

                if self.customer_spinner.text == 'All Customers':
                    story.append(make_section_header("Breakdown by Customer"))
                    # ✅ 10 أعمدة - استخدمنا فونت 6.5 و wordWrap='CJK' لمنع التداخل
                    cust_data = [[
                        P('Customer', 7, color=PDF_COLORS['header_text']),
                        P('Trips', 7, color=PDF_COLORS['header_text']),
                        P('Conc(m3)', 7, color=PDF_COLORS['header_text']),
                        P('Cement', 7, color=PDF_COLORS['header_text']),
                        P('Sand', 7, color=PDF_COLORS['header_text']),
                        P('Agg1', 7, color=PDF_COLORS['header_text']),
                        P('Agg2', 7, color=PDF_COLORS['header_text']),
                        P('Water', 7, color=PDF_COLORS['header_text']),
                        P('Add1', 7, color=PDF_COLORS['header_text']),
                        P('Add2', 7, color=PDF_COLORS['header_text']),
                    ]]
                    for d in self.last_data:
                        cust_data.append([
                            P(d['customer'], 7),
                            P(str(d['trip_count']), 7),
                            P(f"{d['total_concrete']:.1f}", 7),
                            P(f"{d['total_cement']:.0f}", 7),
                            P(f"{d['total_sand']:.0f}", 7),
                            P(f"{d['total_agg1']:.0f}", 7),
                            P(f"{d['total_agg2']:.0f}", 7),
                            P(f"{d['total_water']:.0f}", 7),
                            P(f"{d['total_add1']:.0f}", 7),
                            P(f"{d['total_add2']:.0f}", 7),
                        ])
                    cust_table = Table(
                        cust_data,
                        colWidths=[95, 35, 50, 55, 52, 52, 52, 52, 52, 52])
                    s = build_table_style()
                    add_row_alternating(s, len(cust_data), start=1)
                    cust_table.setStyle(s)
                    story.append(cust_table)

                story.extend(make_signature())
                progress.update_progress(95, "Building PDF...")
                doc.build(story)
                notify_media_scanner(filepath)
                progress.dismiss()
                self.report_text.text = f"PDF saved: {filename}"
                self.show_open_folder_option(filepath)
            except Exception as e:
                log.error("Consumption PDF error: %s", e)
                log.error(traceback.format_exc())
                try:
                    progress.dismiss()
                except Exception:
                    pass
                self.report_text.text = f"PDF error: {str(e)}"

        Clock.schedule_once(generate_pdf, 0.1)

    def show_open_folder_option(self, filepath):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        path_label = Label(
            text=f"PDF saved successfully!\n\nLocation:\n{filepath}\n\nDo you want to open the file?",
            halign='center', color=(0, 0, 0, 1), font_size=dp(12))
        path_label.bind(size=path_label.setter('text_size'))
        content.add_widget(path_label)
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        yes_btn = StyledButton(text="Open File", bg_color=Palette.INFO)
        no_btn = StyledButton(text="Close", bg_color=Palette.NEUTRAL)
        btn_box.add_widget(yes_btn); btn_box.add_widget(no_btn)
        content.add_widget(btn_box)
        popup = LightPopup(title="Success", content=content, size_hint=(0.8, 0.4))
        yes_btn.bind(on_release=lambda x: (open_file_location(filepath), popup.dismiss()))
        no_btn.bind(on_release=popup.dismiss)
        popup.open()


# ============================================================
# ==================== ADVANCED TAB ==========================
# ============================================================
class AdvancedTab(ScrollView):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.do_scroll_x = False
        self.build_ui()

    def build_ui(self):
        main = BoxLayout(orientation='vertical', padding=dp(12),
                         spacing=dp(10), size_hint_y=None)
        main.bind(minimum_height=main.setter('height'))

        btn_layout = GridLayout(cols=2, spacing=dp(10), size_hint_y=None)
        btn_layout.bind(minimum_height=btn_layout.setter('height'))

        btns = [
            ("Manage Drivers", self.manage_drivers, Palette.INFO),
            ("Manage Trucks", self.manage_trucks, Palette.INFO),
            ("Manage Customers", self.manage_customers, Palette.INFO),
            ("Manage Mix Types", self.manage_mixes, Palette.INFO),
            ("Manage Plants", self.manage_plants, Palette.PRIMARY_L),
            ("Edit Trip", self.edit_trip, Palette.WARNING),
            ("Delete Trip", self.delete_trip, Palette.DANGER),
            ("Period Report", self.open_period_report, Palette.INFO),
            ("Consumption Report", self.open_consumption_report, Palette.SUCCESS),
            ("Backup Database", self.backup_database, Palette.PRIMARY),
            ("Reset All Data", self.reset_data, Palette.DANGER),
        ]
        for text, func, color in btns:
            btn = StyledButton(text=text, bg_color=color, font_size=dp(13))
            btn.bind(on_release=func)
            btn_layout.add_widget(btn)
        main.add_widget(btn_layout)

        self.message = Label(text="", size_hint_y=None, height=dp(40),
                             color=(0, 0, 0, 1), font_size=dp(11))
        main.add_widget(self.message)
        self.add_widget(main)

    def manage_drivers(self, instance):
        self._manage_simple_list(
            title="Manage Drivers",
            items=self.app.db.get_drivers(),
            display_fn=lambda d: f"{d['name']} - {d['phone']}",
            delete_fn=lambda did, popup: self._confirm_delete(
                lambda: self.app.db.delete_driver(did),
                "delete_driver", str(did), popup,
                on_done=lambda: self.manage_drivers(None)))

    def manage_trucks(self, instance):
        self._manage_simple_list(
            title="Manage Trucks",
            items=self.app.db.get_trucks(),
            display_fn=lambda t: f"{t['number']} - {t['model']}",
            delete_fn=lambda tid, popup: self._confirm_delete(
                lambda: self.app.db.delete_truck(tid),
                "delete_truck", str(tid), popup,
                on_done=lambda: self.manage_trucks(None)))

    def manage_customers(self, instance):
        self._manage_simple_list(
            title="Manage Customers",
            items=self.app.db.get_customers(),
            display_fn=lambda c: f"{c['name']} - {c['phone']}",
            delete_fn=lambda cid, popup: self._confirm_delete(
                lambda: self.app.db.delete_customer(cid),
                "delete_customer", str(cid), popup,
                on_done=lambda: self.manage_customers(None)))

    def _manage_simple_list(self, title, items, display_fn, delete_fn):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        list_box = BoxLayout(orientation='vertical', size_hint_y=None)
        list_box.bind(minimum_height=list_box.setter('height'))
        popup = LightPopup(title=title, content=content, size_hint=(0.9, 0.7))
        for item in items:
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(5))
            row.add_widget(Label(text=display_fn(item), size_hint_x=0.7,
                                 color=(0, 0, 0, 1), font_size=dp(11)))
            del_btn = ModernButton(text="Delete", size_hint_x=0.3,
                                   bg_color=Palette.DANGER, font_size=dp(11))
            del_btn.bind(on_release=lambda x, i=item: delete_fn(i['id'], popup))
            row.add_widget(del_btn)
            list_box.add_widget(row)
        scroll = ScrollView()
        scroll.add_widget(list_box)
        content.add_widget(scroll)
        close_btn = StyledButton(text="Close", size_hint_y=None, height=dp(45),
                                  font_size=dp(13))
        content.add_widget(close_btn)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()

    def _confirm_delete(self, action_fn, action_name, details, popup, on_done):
        try:
            action_fn()
            self.app.db.log_action(self.app.current_user, action_name, details)
            popup.dismiss()
            if on_done:
                on_done()
        except Exception as e:
            log.error("_confirm_delete failed: %s", e)
            self.message.text = f"Error: {e}"

    def manage_mixes(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        mixes = self.app.db.get_mix_types()
        list_box = BoxLayout(orientation='vertical', size_hint_y=None)
        list_box.bind(minimum_height=list_box.setter('height'))
        popup = LightPopup(title="Manage Mix Types", content=content,
                            size_hint=(0.9, 0.7))
        for m in mixes:
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(5))
            row.add_widget(Label(text=m, size_hint_x=0.7,
                                 color=(0, 0, 0, 1), font_size=dp(11)))
            if m != "Standard":
                del_btn = ModernButton(text="Delete", size_hint_x=0.3,
                                       bg_color=Palette.DANGER, font_size=dp(11))
                del_btn.bind(on_release=lambda x, name=m: self._delete_mix(name, popup))
                row.add_widget(del_btn)
            else:
                row.add_widget(Label(text="(Default)", size_hint_x=0.3,
                                     color=(0, 0, 0, 1), font_size=dp(11)))
            list_box.add_widget(row)
        scroll = ScrollView()
        scroll.add_widget(list_box)
        content.add_widget(scroll)
        close_btn = StyledButton(text="Close", size_hint_y=None, height=dp(45),
                                  font_size=dp(13))
        content.add_widget(close_btn)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()

    def _delete_mix(self, name, popup):
        try:
            self.app.db.delete_mix_type(name)
            self.app.db.log_action(self.app.current_user, "delete_mix", f"Name: {name}")
            main_screen = self.app.root.get_screen('main')
            add_trip = main_screen.contents.get('Add Trip')
            if add_trip:
                add_trip.update_mix_list()
            popup.dismiss()
            self.manage_mixes(None)
        except Exception as e:
            log.error("_delete_mix failed: %s", e)

    def manage_plants(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        plants = self.app.db.get_plants()
        list_box = BoxLayout(orientation='vertical', size_hint_y=None)
        list_box.bind(minimum_height=list_box.setter('height'))
        popup = LightPopup(title="Manage Plants", content=content, size_hint=(0.9, 0.7))
        for p in plants:
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(5))
            row.add_widget(Label(text=f"{p['name']} - {p['location'] or ''}",
                                 size_hint_x=0.5, color=(0, 0, 0, 1), font_size=dp(11)))
            edit_btn = ModernButton(text="Edit", size_hint_x=0.25,
                                    bg_color=Palette.PRIMARY_L, font_size=dp(11))
            del_btn = ModernButton(text="Delete", size_hint_x=0.25,
                                   bg_color=Palette.DANGER, font_size=dp(11))
            edit_btn.bind(on_release=lambda x, pid=p['id']: self._edit_plant(pid, popup))
            del_btn.bind(on_release=lambda x, pid=p['id']: self._delete_plant(pid, popup))
            row.add_widget(edit_btn)
            row.add_widget(del_btn)
            list_box.add_widget(row)
        scroll = ScrollView()
        scroll.add_widget(list_box)
        content.add_widget(scroll)
        add_btn = StyledButton(text="Add New Plant", size_hint_y=None, height=dp(45))
        add_btn.bind(on_release=self._add_plant)
        content.add_widget(add_btn)
        close_btn = StyledButton(text="Close", size_hint_y=None, height=dp(45),
                                  font_size=dp(13))
        content.add_widget(close_btn)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()

    def _add_plant(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        name_input = StyledTextInput(hint_text="Plant Name")
        loc_input = StyledTextInput(hint_text="Location (optional)")
        save_btn = StyledButton(text="Add", bg_color=Palette.SUCCESS)
        content.add_widget(Label(text="New Plant", color=(0, 0, 0, 1), font_size=dp(13)))
        content.add_widget(name_input)
        content.add_widget(loc_input)
        content.add_widget(save_btn)
        popup = LightPopup(title="Add Plant", content=content, size_hint=(0.8, 0.4))

        def save(btn):
            name = name_input.text.strip()
            if name:
                pid = self.app.db.add_plant(name, loc_input.text.strip())
                if pid:
                    self.message.text = f"Plant '{name}' added"
                    popup.dismiss()
                    self.manage_plants(None)
                else:
                    self.message.text = "Plant name already exists"

        save_btn.bind(on_release=save)
        popup.open()

    def _edit_plant(self, plant_id, parent_popup):
        plant = next((p for p in self.app.db.get_plants() if p['id'] == plant_id), None)
        if not plant:
            return
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        name_input = StyledTextInput(text=plant['name'])
        loc_input = StyledTextInput(text=plant['location'] or "")
        save_btn = StyledButton(text="Update", bg_color=Palette.SUCCESS)
        content.add_widget(Label(text="Edit Plant", color=(0, 0, 0, 1), font_size=dp(13)))
        content.add_widget(name_input)
        content.add_widget(loc_input)
        content.add_widget(save_btn)
        popup = LightPopup(title="Edit Plant", content=content, size_hint=(0.8, 0.4))

        def save(btn):
            name = name_input.text.strip()
            if name:
                if self.app.db.update_plant(plant_id, name, loc_input.text.strip()):
                    self.message.text = f"Plant '{name}' updated"
                    parent_popup.dismiss()
                    self.manage_plants(None)
                else:
                    self.message.text = "Plant name already exists"

        save_btn.bind(on_release=save)
        popup.open()

    def _delete_plant(self, plant_id, parent_popup):
        success, msg = self.app.db.delete_plant(plant_id)
        if success:
            self.app.db.log_action(self.app.current_user, "delete_plant", f"ID: {plant_id}")
            parent_popup.dismiss()
            self.manage_plants(None)
        else:
            self.message.text = msg

    def open_period_report(self, instance):
        content = ReportsTab(self.app)
        popup = LightPopup(title="Period Report", content=content, size_hint=(0.95, 0.9))
        popup.open()

    def open_consumption_report(self, instance):
        content = CustomerConsumptionTab(self.app)
        popup = LightPopup(title="Customer Consumption Report", content=content,
                            size_hint=(0.95, 0.9))
        popup.open()

    def edit_trip(self, instance):
        main_screen = self.app.root.get_screen('main')
        trips = self.app.db.get_trips(plant_id=main_screen.current_plant_id, limit=100)
        if not trips:
            self.message.text = "No trips to edit"
            return

        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(15))
        content.add_widget(Label(text="Select Trip to Edit:", color=(0, 0, 0, 1),
                                  font_size=dp(13)))
        trip_options = [f"{t['id']}: {t['date']} {t['time']} - {t['customer']} - {t['quantity']} m3"
                        for t in trips]
        trip_spinner = StyledSpinner(text='Select Trip', values=trip_options,
                                      font_size=dp(13))
        content.add_widget(trip_spinner)

        content.add_widget(Label(text="New Customer:", color=(0, 0, 0, 1), font_size=dp(13)))
        new_customer = StyledSpinner(text='Select Customer',
                                     values=self.get_customer_names(), font_size=dp(13))
        content.add_widget(new_customer)

        content.add_widget(Label(text="New Quantity (m3):", color=(0, 0, 0, 1),
                                  font_size=dp(13)))
        new_qty = StyledTextInput(hint_text="Leave empty to keep current",
                                   font_size=dp(13))
        content.add_widget(new_qty)

        content.add_widget(Label(text="New Mix Type:", color=(0, 0, 0, 1), font_size=dp(13)))
        new_mix = StyledSpinner(text='Select Mix',
                                values=self.app.db.get_mix_types(), font_size=dp(13))
        content.add_widget(new_mix)

        content.add_widget(Label(text="New Truck:", color=(0, 0, 0, 1), font_size=dp(13)))
        new_truck = StyledSpinner(text='Select Truck',
                                  values=self.get_truck_numbers(), font_size=dp(13))
        content.add_widget(new_truck)

        content.add_widget(Label(text="New Driver:", color=(0, 0, 0, 1), font_size=dp(13)))
        new_driver = StyledSpinner(text='Select Driver',
                                   values=self.get_driver_names(), font_size=dp(13))
        content.add_widget(new_driver)

        content.add_widget(Label(text="New Pump Number:", color=(0, 0, 0, 1), font_size=dp(13)))
        new_pump = StyledTextInput(hint_text="Leave empty to keep current",
                                    font_size=dp(13))
        content.add_widget(new_pump)

        content.add_widget(Label(text="New Primary Silo (1-4):", color=(0, 0, 0, 1),
                                  font_size=dp(13)))
        new_silo = StyledTextInput(hint_text="Leave empty to keep current",
                                    font_size=dp(13))
        content.add_widget(new_silo)

        content.add_widget(Label(text="Secondary Silo (if any):", color=(0, 0, 0, 1),
                                  font_size=dp(13)))
        new_sec_silo = StyledTextInput(hint_text="Leave empty to keep current",
                                        font_size=dp(13))
        content.add_widget(new_sec_silo)

        plants = self.app.db.get_plants()
        plant_options = [p['name'] for p in plants]
        content.add_widget(Label(text="Move to Plant:", color=(0, 0, 0, 1), font_size=dp(13)))
        new_plant_spinner = StyledSpinner(text='Keep current',
                                          values=['Keep current'] + plant_options,
                                          font_size=dp(13))
        content.add_widget(new_plant_spinner)

        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        save_btn = StyledButton(text="Save Changes", bg_color=Palette.SUCCESS,
                                font_size=dp(13))
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.DANGER,
                                   font_size=dp(13))
        btn_box.add_widget(save_btn)
        btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)

        popup = LightPopup(title="Edit Trip", content=content, size_hint=(0.9, 0.95))

        def save(btn):
            selected = trip_spinner.text
            if selected == 'Select Trip':
                self.message.text = "Please select a trip"
                return
            try:
                trip_id = int(selected.split(':')[0])
            except Exception:
                self.message.text = "Invalid selection"
                return
            updates = {}
            if new_customer.text != 'Select Customer':
                updates['customer'] = new_customer.text
            if new_qty.text.strip():
                try:
                    q = float(new_qty.text.strip())
                    if not (MIN_QTY_PER_TRIP <= q <= MAX_QTY_PER_TRIP):
                        self.message.text = "Quantity out of range"
                        return
                    updates['quantity'] = q
                except ValueError:
                    self.message.text = "Invalid quantity"
                    return
            if new_mix.text != 'Select Mix':
                updates['mix'] = new_mix.text
            if new_truck.text != 'Select Truck':
                updates['truck'] = new_truck.text
            if new_driver.text != 'Select Driver':
                updates['driver'] = new_driver.text
            if new_pump.text.strip():
                updates['pump'] = new_pump.text.strip()
            if new_silo.text.strip():
                try:
                    s = int(new_silo.text.strip())
                    if 1 <= s <= 4:
                        updates['silo'] = s
                    else:
                        self.message.text = "Primary silo must be 1-4"
                        return
                except ValueError:
                    self.message.text = "Invalid primary silo number"
                    return
            if new_sec_silo.text.strip():
                try:
                    s2 = int(new_sec_silo.text.strip())
                    if 1 <= s2 <= 4:
                        updates['secondary_silo'] = s2
                    else:
                        self.message.text = "Secondary silo must be 1-4"
                        return
                except ValueError:
                    self.message.text = "Invalid secondary silo number"
                    return

            if new_plant_spinner.text != 'Keep current':
                for p in plants:
                    if p['name'] == new_plant_spinner.text:
                        updates['plant_id'] = p['id']
                        break

            if updates:
                success, msg = self.app.db.update_trip(trip_id, updates,
                                                        self.app.current_user)
                if success:
                    self.message.text = msg
                    self.app.root.get_screen('main').update_dashboard()
                    popup.dismiss()
                else:
                    self.message.text = f"Error: {msg}"
            else:
                self.message.text = "No changes made"

        save_btn.bind(on_release=save)
        cancel_btn.bind(on_release=popup.dismiss)
        popup.open()

    def delete_trip(self, instance):
        main_screen = self.app.root.get_screen('main')
        trips = self.app.db.get_trips(plant_id=main_screen.current_plant_id, limit=100)
        if not trips:
            self.message.text = "No trips to delete"
            return

        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(15))
        content.add_widget(Label(text="Select Trip to Delete:", color=(0, 0, 0, 1),
                                  font_size=dp(13)))
        trip_options = [f"{t['id']}: {t['date']} {t['time']} - {t['customer']} - {t['quantity']} m3"
                        for t in trips]
        trip_spinner = StyledSpinner(text='Select Trip', values=trip_options,
                                      font_size=dp(13))
        content.add_widget(trip_spinner)
        content.add_widget(Label(text="WARNING: This action cannot be undone!",
                                 color=Palette.DANGER, bold=True))
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        delete_btn = StyledButton(text="Delete Trip", bg_color=Palette.DANGER,
                                   font_size=dp(13))
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.NEUTRAL,
                                   font_size=dp(13))
        btn_box.add_widget(delete_btn)
        btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)
        popup = LightPopup(title="Delete Trip", content=content, size_hint=(0.85, 0.4))

        def do_delete(btn):
            selected = trip_spinner.text
            if selected == 'Select Trip':
                self.message.text = "Please select a trip"
                return
            try:
                trip_id = int(selected.split(':')[0])
            except Exception:
                self.message.text = "Invalid selection"
                return
            success, msg = self.app.db.delete_trip(trip_id, self.app.current_user)
            if success:
                self.message.text = msg
                self.app.root.get_screen('main').update_dashboard()
                popup.dismiss()
            else:
                self.message.text = f"Error: {msg}"

        delete_btn.bind(on_release=do_delete)
        cancel_btn.bind(on_release=popup.dismiss)
        popup.open()

    def get_truck_numbers(self):
        return [t['number'] for t in self.app.db.get_trucks()] or ['No trucks']

    def get_driver_names(self):
        return [d['name'] for d in self.app.db.get_drivers()] or ['No drivers']

    def get_customer_names(self):
        return [c['name'] for c in self.app.db.get_customers()] or ['No customers']

    def backup_database(self, instance):
        try:
            path = self.app.db.backup_database()
            self.app.db.log_action(self.app.current_user, "backup",
                                    f"File: {os.path.basename(path)}")
            self.message.text = f"Backup created: {os.path.basename(path)}"
        except Exception as e:
            log.error("Backup failed: %s", e)
            self.message.text = f"Backup failed: {e}"

    def reset_data(self, instance):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(12))
        content.add_widget(Label(text="WARNING: This will delete ALL data!",
                                 color=Palette.DANGER, bold=True))
        content.add_widget(Label(text="This action cannot be undone.",
                                 color=(0, 0, 0, 1)))
        btn_box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        confirm_btn = StyledButton(text="RESET ALL", bg_color=Palette.DANGER,
                                    font_size=dp(13))
        cancel_btn = StyledButton(text="Cancel", bg_color=Palette.NEUTRAL,
                                   font_size=dp(13))
        btn_box.add_widget(confirm_btn); btn_box.add_widget(cancel_btn)
        content.add_widget(btn_box)
        popup2 = LightPopup(title="Reset All Data", content=content, size_hint=(0.8, 0.3))

        def reset(btn):
            try:
                c = self.app.db.conn.cursor()
                for table in ('trips', 'cement_supplies', 'silo_balances',
                              'shift_start_balances', 'audit_log',
                              'customer_consumption'):
                    c.execute(f"DELETE FROM {table}")
                self.app.db.conn.commit()
                self.app.db.log_action(self.app.current_user, "reset_all_data",
                                        "All production data cleared")
                self.message.text = "All data reset"
                popup2.dismiss()
            except Exception as e:
                log.error("Reset failed: %s", e)
                self.message.text = f"Reset failed: {e}"

        confirm_btn.bind(on_release=reset)
        cancel_btn.bind(on_release=popup2.dismiss)
        popup2.open()


# ============================================================
# ==================== MAIN SCREEN ===========================
# ============================================================
class MainScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.current_shift_date = get_shift_date()
        self.current_plant_id = None
        self.current_tab_name = 'Add Trip'
        self._dashboard_event = None
        self.contents = {}
        self.build_ui()
        Clock.schedule_once(lambda dt: self.initialize_plant(), 0.5)

    def initialize_plant(self):
        try:
            plants = self.app.db.get_plants()
            if not plants:
                self.app.db.add_plant("Main Plant", "Default")
                plants = self.app.db.get_plants()
            self.plant_spinner.values = [p['name'] for p in plants]
            if self.current_plant_id is None and plants:
                self.current_plant_id = plants[0]['id']
            for p in plants:
                if p['id'] == self.current_plant_id:
                    self.plant_spinner.text = p['name']
                    break
            self.update_shift_list()
            self.shift_info_label.text = get_shift_range_text(self.current_shift_date)
            self.rebuild_contents()
            self.update_dashboard()
            if self._dashboard_event:
                self._dashboard_event.cancel()
            self._dashboard_event = Clock.schedule_once(
                lambda dt: self.update_dashboard(), 0.5)
        except Exception as e:
            log.error("initialize_plant failed: %s", e)
            log.error(traceback.format_exc())

    def build_ui(self):
        root = BoxLayout(orientation='vertical')
        root.add_widget(ModernHeader(title="5M System",
                                     subtitle="Production Management"))

        top = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(8),
                        padding=[dp(10), dp(6)])
        plant_card = Card(orientation='horizontal', padding=dp(6),
                          spacing=dp(6), size_hint_x=0.5)
        plant_card.add_widget(Label(text="PL", font_size=sp(12), bold=True,
                                     size_hint_x=None, width=dp(30),
                                     color=Palette.PRIMARY))
        self.plant_spinner = Spinner(
            text='Select Plant', values=[],
            background_normal='', background_down='',
            background_color=Palette.SURFACE,
            color=Palette.TEXT, font_size=sp(13))
        self.plant_spinner.bind(text=self.on_plant_change)
        plant_card.add_widget(self.plant_spinner)

        shift_card = Card(orientation='horizontal', padding=dp(6),
                          spacing=dp(6), size_hint_x=0.5)
        shift_card.add_widget(Label(text="SH", font_size=sp(12), bold=True,
                                     size_hint_x=None, width=dp(30),
                                     color=Palette.PRIMARY))
        self.shift_spinner = Spinner(
            text=self.current_shift_date, values=[],
            background_normal='', background_down='',
            background_color=Palette.SURFACE,
            color=Palette.TEXT, font_size=sp(13))
        self.shift_spinner.bind(text=self.on_shift_change)
        shift_card.add_widget(self.shift_spinner)
        date_btn = ModernButton(text="Date", bg_color=Palette.PRIMARY_L,
                                size_hint_x=None, width=dp(56),
                                size_hint_y=None, height=dp(36),
                                font_size=sp(11))
        date_btn.bind(on_release=self.open_date_picker)
        shift_card.add_widget(date_btn)

        top.add_widget(plant_card)
        top.add_widget(shift_card)
        root.add_widget(top)

        self.shift_info_label = Label(
            text="", size_hint_y=None, height=dp(22),
            color=Palette.TEXT_MUTED, font_size=sp(10.5))
        root.add_widget(self.shift_info_label)

        kpi_bar = BoxLayout(size_hint_y=None, height=dp(58),
                            spacing=dp(8), padding=[dp(10), dp(2)])

        def make_kpi(label, color):
            card = Card(orientation='vertical', padding=dp(8), spacing=dp(2))
            t = Label(text=label, font_size=sp(9), color=Palette.TEXT_MUTED,
                      halign='left', valign='bottom',
                      size_hint_y=None, height=dp(14))
            v = Label(text="0", font_size=sp(15), bold=True, color=color,
                      halign='left', valign='top')
            for w in (t, v):
                w.bind(size=lambda i, s: setattr(i, 'text_size', (s[0], s[1])))
            card.add_widget(t)
            card.add_widget(v)
            return card, v

        c1, self.kpi_trips = make_kpi("Trips", Palette.INFO)
        c2, self.kpi_conc  = make_kpi("Concrete m3", Palette.SUCCESS)
        c3, self.kpi_cem   = make_kpi("Cement kg", Palette.WARNING)

        kpi_bar.add_widget(c1)
        kpi_bar.add_widget(c2)
        kpi_bar.add_widget(c3)
        root.add_widget(kpi_bar)

        self.silo_cards_box = BoxLayout(size_hint_y=None, height=dp(110),
                                        spacing=dp(8),
                                        padding=[dp(10), dp(4)])
        self.silo_cards = []
        for i in range(4):
            c = Card(orientation='vertical', padding=dp(10), spacing=dp(2))
            title = Label(text=f"Silo {i+1}", font_size=sp(11), bold=True,
                          color=Palette.PRIMARY, size_hint_y=None, height=dp(18))
            value = Label(text="0 kg", font_size=sp(15), bold=True,
                          color=Palette.TEXT, size_hint_y=None, height=dp(24))
            pct = Label(text="0%", font_size=sp(10),
                        color=Palette.TEXT_MUTED, size_hint_y=None, height=dp(16))
            bar = SiloBar()
            c.add_widget(title)
            c.add_widget(value)
            c.add_widget(pct)
            c.add_widget(bar)
            self.silo_cards_box.add_widget(c)
            self.silo_cards.append({'value': value, 'pct': pct, 'bar': bar})
        root.add_widget(self.silo_cards_box)

        self.warning_label = Label(text="", size_hint_y=None, height=dp(22),
                                    color=Palette.DANGER, font_size=sp(11), bold=True)
        root.add_widget(self.warning_label)

        self.current_content = BoxLayout(size_hint_y=1)
        root.add_widget(self.current_content)

        small_bar = BoxLayout(size_hint_y=None, height=dp(28),
                              padding=[dp(10), dp(2)], spacing=dp(6))
        self.role_label = Label(text="Full Admin Mode", font_size=sp(10),
                                 color=Palette.TEXT_MUTED,
                                 halign='left', valign='middle')
        small_bar.add_widget(self.role_label)
        root.add_widget(small_bar)

        self.tabbar = TabBar(
            tabs=[
                ('Add Trip', 'Add Trip'),
                ('Search', 'Search'),
                ('Cement', 'Cement'),
                ('Mix Types', 'Mixes'),
                ('Advanced', 'Advanced'),
            ],
            on_select=self.switch_tab)
        root.add_widget(self.tabbar)

        self.add_widget(root)

    def rebuild_contents(self):
        if self.current_plant_id is None:
            return
        self.contents = {
            'Add Trip':  AddTripTab(self.app, self.current_shift_date, self.current_plant_id),
            'Search':    SearchTab(self.app, self.current_plant_id),
            'Cement':    CementTab(self.app, self.current_shift_date, self.current_plant_id),
            'Mix Types': MixTypesTab(self.app),
            'Advanced':  AdvancedTab(self.app),
        }
        self.switch_tab(self.current_tab_name)

    def on_plant_change(self, instance, value):
        try:
            for p in self.app.db.get_plants():
                if p['name'] == value:
                    self.current_plant_id = p['id']
                    break
            self.update_shift_list()
            self.rebuild_contents()
            self.update_dashboard()
            self.shift_info_label.text = get_shift_range_text(self.current_shift_date)
        except Exception as e:
            log.error("on_plant_change failed: %s", e)

    def get_available_shifts(self):
        if self.current_plant_id is None:
            return [self.current_shift_date]
        try:
            c = self.app.db.conn.cursor()
            c.execute("""SELECT DISTINCT shift_date FROM trips WHERE plant_id=?
                         UNION SELECT shift_date FROM cement_supplies WHERE plant_id=?
                         ORDER BY shift_date DESC""",
                      (self.current_plant_id, self.current_plant_id))
            shifts = [row[0] for row in c.fetchall()]
        except Exception as e:
            log.warning("get_available_shifts failed: %s", e)
            shifts = []
        if not shifts or self.current_shift_date not in shifts:
            shifts.insert(0, self.current_shift_date)
        return shifts

    def update_shift_list(self):
        self.shift_spinner.values = self.get_available_shifts()

    def on_shift_change(self, instance, value):
        if not value:
            return
        try:
            self.current_shift_date = value
            self.shift_info_label.text = get_shift_range_text(value)
            self.update_dashboard()
            if hasattr(self, 'contents') and self.contents:
                if 'Add Trip' in self.contents:
                    self.contents['Add Trip'] = AddTripTab(
                        self.app, self.current_shift_date, self.current_plant_id)
                if 'Cement' in self.contents:
                    self.contents['Cement'] = CementTab(
                        self.app, self.current_shift_date, self.current_plant_id)
            if self.current_tab_name in ('Add Trip', 'Cement'):
                self.switch_tab(self.current_tab_name)
        except Exception as e:
            log.error("on_shift_change failed: %s", e)

    def open_date_picker(self, instance):
        def on_date_selected(date_str):
            self.current_shift_date = date_str
            self.shift_spinner.text = date_str
            self.on_shift_change(None, date_str)
        DatePickerPopup(on_select=on_date_selected).open()

    def switch_tab(self, tab_name):
        if getattr(self, '_switching_tab', False):
            return
        if (getattr(self, 'current_tab_name', None) == tab_name
                and self.current_content.children):
            return
        self._switching_tab = True
        try:
            self.current_content.clear_widgets()
            if tab_name in self.contents:
                self.current_content.add_widget(self.contents[tab_name])
            self.current_tab_name = tab_name
            if hasattr(self, 'tabbar'):
                self.tabbar.set_active(tab_name)
        finally:
            self._switching_tab = False

    def update_dashboard(self, dt=None):
        if self.current_plant_id is None:
            return
        try:
            balances = self.app.db.calculate_balance(
                self.current_plant_id, self.current_shift_date)
            trips = self.app.db.get_trips(plant_id=self.current_plant_id,
                                           shift_date=self.current_shift_date)
            total_conc = sum(t['quantity'] for t in trips)
            total_cem = sum(t['cement'] + (t.get('secondary_cement') or 0)
                            for t in trips)
            try:
                self.kpi_trips.text = str(len(trips))
                self.kpi_conc.text = f"{total_conc:,.1f}"
                self.kpi_cem.text = f"{total_cem:,.0f}"
            except Exception:
                pass

            for i, card in enumerate(self.silo_cards):
                bal = balances[i] if i < len(balances) else 0
                cap = SILO_CAPACITIES[i + 1]
                pct = (bal / cap) * 100 if cap else 0
                card['value'].text = f"{bal:,.0f} kg"
                card['pct'].text = f"{pct:.0f}%"
                if pct < 15:
                    color = Palette.DANGER
                elif pct < 30:
                    color = Palette.WARNING
                else:
                    color = Palette.SUCCESS
                bar = card['bar']
                bar.bar_color = color
                bar.progress = max(0.0, min(1.0, pct / 100.0))

            if balances and len(balances) >= 4 and min(balances[:4]) < LOW_CEMENT_THRESHOLD:
                self.warning_label.text = (f"LOW CEMENT WARNING - "
                                            f"Silo below {LOW_CEMENT_THRESHOLD:,} kg")
            else:
                self.warning_label.text = ""
        except sqlite3.OperationalError as e:
            if "no such column" in str(e):
                self.warning_label.text = "Upgrading database... Please restart."
                try:
                    self.app.db._migrate_tables()
                except Exception:
                    pass
            else:
                self.warning_label.text = f"Error: {e}"
        except Exception as e:
            log.error("update_dashboard error: %s", e)
            self.warning_label.text = f"Error: {e}"

    def on_enter(self):
        self.role_label.text = "Full Admin Mode"


# ============================================================
# ===================== MAIN APP =============================
# ============================================================
class ConcreteApp(App):
    def build(self):
        self.db = Database()
        self.db.connect()
        self.current_user = "admin"
        self.current_role = "admin"

        sm = ScreenManager()
        sm.add_widget(MainScreen(name='main', app=self))
        return sm

    def on_start(self):
        if platform == 'android' and request_permissions is not None:
            try:
                request_permissions([Permission.WRITE_EXTERNAL_STORAGE,
                                     Permission.READ_EXTERNAL_STORAGE])
            except Exception as e:
                log.warning("Permission request failed: %s", e)

    def has_permission(self, action):
        return True

    def ask_password(self, on_success, title="Confirm"):
        try:
            on_success()
        except Exception as e:
            log.error("ask_password callback failed: %s", e)

    def on_stop(self):
        self.db.disconnect()


if __name__ == '__main__':
    ConcreteApp().run()