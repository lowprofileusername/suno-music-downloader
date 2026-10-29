# -*- coding: utf-8 -*-
"""Suno 音乐下载器 · Windows 桌面版（PySide6 / Qt6，高分屏自适应）。

启动：
    python suno_gui.py
或双击技能目录下的「启动 Suno 下载器.bat」。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# --- 高分屏：必须在 QApplication 创建前设置 -------------------------------
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PySide6.QtCore import Qt, QSettings, QThread, Signal  # noqa: E402
from PySide6.QtGui import QFont, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

try:
    from suno_core import (BlockedError, NotFoundError, SunoClient, SunoError,
                           parse_inputs, resolve, sanitize, save_track)
except ImportError as e:  # 依赖缺失要如实告知，不要静默降级
    raise SystemExit(f"缺少依赖或核心库：{e}\n请先运行：pip install requests cryptography imageio-ffmpeg PySide6-Essentials")

if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

STYLE = """
QWidget { background:#15171c; color:#e7e9ef; font-family:"Microsoft YaHei UI"; font-size:10pt; }
QMainWindow, QDialog { background:#15171c; }
QLabel#title { font-size:15pt; font-weight:600; color:#ffffff; }
QLabel#hint  { color:#9aa0ad; font-size:9pt; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {
    background:#1e2129; border:1px solid #2f3440; border-radius:6px;
    padding:6px; selection-background-color:#3d6fd6;
}
QLineEdit:focus, QPlainTextEdit:focus { border:1px solid #4b7fe0; }
QPushButton {
    background:#2b6cb0; color:#ffffff; border:none; border-radius:6px;
    padding:7px 16px; font-weight:600;
}
QPushButton:hover   { background:#3182ce; }
QPushButton:pressed { background:#2c5282; }
QPushButton:disabled{ background:#333842; color:#7b8290; }
QPushButton#ghost { background:#262a33; color:#cfd4de; }
QPushButton#ghost:hover { background:#2f343f; }
QProgressBar { background:#1e2129; border:none; border-radius:5px; height:14px; text-align:center; }
QProgressBar::chunk { background:#3d8bfd; border-radius:5px; }
QTableWidget { background:#1a1d24; border:1px solid #2f3440; border-radius:6px; gridline-color:#2f3440; }
QHeaderView::section { background:#22262f; color:#c9cfda; padding:6px; border:none; }
QCheckBox { spacing:6px; }
QGroupBox { border:1px solid #2f3440; border-radius:6px; margin-top:12px; padding-top:6px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; color:#9aa0ad; }
"""


class ResolveWorker(QThread):
    done = Signal(object)          # list[Track]
    failed = Signal(str)

    def __init__(self, text: str, proxy: str | None = None, parent=None):
        super().__init__(parent)
        self.text, self.proxy = text, proxy

    def run(self):
        try:
            client = SunoClient(proxy=self.proxy)
            items = parse_inputs(self.text)
            if not items:
                self.failed.emit("没有识别出 Suno 链接。支持 suno.com/s/xxx、/song/xxx、/playlist/xxx、/@作者 或裸 uuid")
                return
            self.done.emit(resolve(client, items))
        except (BlockedError, NotFoundError, SunoError) as e:
            self.failed.emit(str(e))


class DownloadWorker(QThread):
    row = Signal(int, int, str)    # row, percent, text
    log = Signal(str)
    finished = Signal(int, int)    # ok, fail

    def __init__(self, tracks, out_dir: Path, fmt: str, cover: bool,
                 lyrics: bool, skip: bool, proxy: str | None = None, parent=None):
        super().__init__(parent)
        self.tracks, self.out_dir, self.fmt = tracks, out_dir, fmt
        self.cover, self.lyrics, self.skip = cover, lyrics, skip
        self.proxy = proxy
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        client = SunoClient(proxy=self.proxy)
        ok = fail = 0
        for i, t in enumerate(self.tracks):
            if self._cancel:
                self.row.emit(i, 0, "已取消")
                continue
            target = self.out_dir / sanitize(t.title)
            probe = target.with_suffix(".mp3" if self.fmt in ("mp3", "both") else ".m4a")
            if self.skip and probe.exists():
                self.row.emit(i, 100, "跳过（已存在）")
                self.log.emit(f"跳过：{t.title}")
                continue
            self.row.emit(i, 0, "取流中…")
            self.log.emit(f"[{i+1}/{len(self.tracks)}] {t.title} ({t.mmss})")
            t0 = time.time()
            try:
                files = save_track(
                    client, t, self.out_dir, fmt=self.fmt,
                    cover=self.cover, lyrics=self.lyrics,
                    progress=lambda d, tot, i=i: self.row.emit(
                        i, int(d * 100 / tot) if tot else 0, "取流 %d%%" % (d * 100 // tot if tot else 0)),
                    cancelled=lambda: self._cancel)
            except BlockedError as e:
                self.row.emit(i, 0, "被限流")
                self.log.emit(f"停止：{e}")
                fail += 1
                break
            except (NotFoundError, SunoError) as e:
                self.row.emit(i, 0, "失败")
                self.log.emit(f"失败：{t.title} —— {e}")
                fail += 1
                continue
            self.row.emit(i, 100, "完成")
            self.log.emit(f"  完成 {time.time()-t0:.1f}s → " + "、".join(f.name for f in files))
            ok += 1
        self.finished.emit(ok, fail)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Suno 音乐下载器")
        self.resize(880, 720)
        self.setMinimumSize(720, 620)
        self.settings = QSettings("SunoDownloader", "GUI")
        self.tracks: list = []
        self.workers: list[QThread] = []

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)

        title = QLabel("Suno 音乐下载器")
        title.setObjectName("title")
        hint = QLabel("粘贴 Suno 链接，支持单曲 / 分享短链 / 播放列表 / 作者主页；匿名下载，无需登录")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(title)
        v.addWidget(hint)

        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("每行一个链接，例如：\nhttps://suno.com/s/6wTAMkQkvmlbMWQD\nhttps://suno.com/playlist/…\nhttps://suno.com/@作者")
        self.links.setFixedHeight(96)
        v.addWidget(self.links)

        opts = QGroupBox("下载设置")
        form = QFormLayout(opts)
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        row_dir = QWidget()
        hd = QHBoxLayout(row_dir)
        hd.setContentsMargins(0, 0, 0, 0)
        hd.setSpacing(8)
        self.dir_edit = QLineEdit(self.settings.value(
            "out", str(Path.home() / "Music" / "Suno"), type=str))
        btn_dir = QPushButton("浏览…")
        btn_dir.setObjectName("ghost")
        btn_dir.clicked.connect(self.pick_dir)
        hd.addWidget(self.dir_edit, 1)
        hd.addWidget(btn_dir)
        form.addRow("输出目录", row_dir)

        row_fmt = QWidget()
        hf = QHBoxLayout(row_fmt)
        hf.setContentsMargins(0, 0, 0, 0)
        hf.setSpacing(16)
        self.fmt = QComboBox()
        self.fmt.addItems(["MP3（推荐，带封面与标签）", "M4A（原始音频）", "两种都要"])
        self.fmt.setCurrentIndex(int(self.settings.value("fmt", 0, type=int)))
        self.chk_cover = QCheckBox("保存封面")
        self.chk_cover.setChecked(self.settings.value("cover", True, type=bool))
        self.chk_lyrics = QCheckBox("保存歌词")
        self.chk_lyrics.setChecked(self.settings.value("lyrics", True, type=bool))
        self.chk_skip = QCheckBox("跳过已存在")
        self.chk_skip.setChecked(self.settings.value("skip", True, type=bool))
        for w in (self.chk_cover, self.chk_lyrics, self.chk_skip):
            hf.addWidget(w)
        hf.addStretch(1)
        form.addRow("输出格式", self.fmt)
        form.addRow("", row_fmt)
        v.addWidget(opts)

        row_btn = QWidget()
        hb = QHBoxLayout(row_btn)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(10)
        self.btn_parse = QPushButton("解析链接")
        self.btn_run = QPushButton("开始下载")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("ghost")
        self.btn_open = QPushButton("打开输出目录")
        self.btn_open.setObjectName("ghost")
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        for b in (self.btn_parse, self.btn_run, self.btn_cancel, self.btn_open):
            hb.addWidget(b)
        hb.addStretch(1)
        v.addWidget(row_btn)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["曲目", "时长", "进度", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c, w in ((1, 70), (2, 160), (3, 130)):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, w)
        self.table.setMinimumHeight(200)
        v.addWidget(self.table, 1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setPlaceholderText("运行日志")
        v.addWidget(self.log)

        self.status = QLabel("就绪")
        self.status.setObjectName("hint")
        v.addWidget(self.status)

        self.btn_parse.clicked.connect(self.do_parse)
        self.btn_run.clicked.connect(self.do_run)
        self.btn_cancel.clicked.connect(self.do_cancel)
        self.btn_open.clicked.connect(self.open_dir)

    # ---------- 交互 ----------
    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def open_dir(self):
        p = Path(self.dir_edit.text())
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))  # noqa: S606

    def _fmt_key(self) -> str:
        return ["mp3", "m4a", "both"][self.fmt.currentIndex()]

    def _proxy(self):
        return self.settings.value("proxy", "", type=str) or None

    def _save_settings(self):
        self.settings.setValue("out", self.dir_edit.text())
        self.settings.setValue("fmt", self.fmt.currentIndex())
        self.settings.setValue("cover", self.chk_cover.isChecked())
        self.settings.setValue("lyrics", self.chk_lyrics.isChecked())
        self.settings.setValue("skip", self.chk_skip.isChecked())

    def do_parse(self):
        text = self.links.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "提示", "请先粘贴至少一个 Suno 链接")
            return
        self.btn_parse.setEnabled(False)
        self.status.setText("正在解析链接…")
        w = ResolveWorker(text, self._proxy(), self)
        w.done.connect(self.on_resolved)
        w.failed.connect(self.on_failed)
        self.workers.append(w)
        w.start()

    def on_resolved(self, tracks):
        self.tracks = tracks
        self.table.setRowCount(len(tracks))
        for i, t in enumerate(tracks):
            self.table.setItem(i, 0, QTableWidgetItem(t.title))
            self.table.setItem(i, 1, QTableWidgetItem(t.mmss))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("%p%")
            self.table.setCellWidget(i, 2, bar)
            self.table.setItem(i, 3, QTableWidgetItem("待下载"))
        self.btn_parse.setEnabled(True)
        self.btn_run.setEnabled(bool(tracks))
        if not tracks:
            self.status.setText("没有解析到任何曲目（可能是未公开或链接类型不支持）")
        else:
            self.status.setText(f"解析到 {len(tracks)} 首，点「开始下载」")

    def on_failed(self, msg):
        self.btn_parse.setEnabled(True)
        self.status.setText("解析失败")
        self.log.append(msg)
        QMessageBox.warning(self, "解析失败", msg)

    def do_run(self):
        if not self.tracks:
            return
        self._save_settings()
        out_dir = Path(self.dir_edit.text()).expanduser()
        self.btn_run.setEnabled(False)
        self.btn_parse.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        w = DownloadWorker(self.tracks, out_dir, self._fmt_key(),
                           self.chk_cover.isChecked(), self.chk_lyrics.isChecked(),
                           self.chk_skip.isChecked(), self._proxy(), self)
        w.row.connect(self.on_row)
        w.log.connect(self.log.append)
        w.finished.connect(self.on_done)
        self.workers.append(w)
        w.start()

    def do_cancel(self):
        for w in self.workers:
            if isinstance(w, DownloadWorker) and w.isRunning():
                w.cancel()
        self.btn_cancel.setEnabled(False)
        self.status.setText("正在取消…")

    def on_row(self, row, pct, text):
        bar = self.table.cellWidget(row, 2)
        if bar:
            bar.setValue(pct)
        item = self.table.item(row, 3)
        item = item or QTableWidgetItem()
        item.setText(text)
        self.table.setItem(row, 3, item)

    def on_done(self, ok, fail):
        self.btn_run.setEnabled(True)
        self.btn_parse.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.status.setText(f"完成：成功 {ok} 首，失败 {fail} 首 → {self.dir_edit.text()}")
        self.log.append(f"—— 成功 {ok} 首，失败 {fail} 首")

    def closeEvent(self, e):
        for w in self.workers:
            if w.isRunning():
                if isinstance(w, DownloadWorker):
                    w.cancel()
                w.wait(3000)
        self._save_settings()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei UI", 10)
    app.setFont(font)
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
