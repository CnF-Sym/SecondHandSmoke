# Second Hand Smoke — verified card offload for camera and sound media.
# Copyright (C) 2026  CnF Works
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <https://www.gnu.org/licenses/>.

"""The day report as a branded PDF.

Producers want the day's paperwork with the production's logo on it, and a PDF
is what gets attached to an email and filed. This is an addition: the HTML
report is unchanged and still written exactly as before.

The important property is that this file cannot form an opinion about media.
It never touches the catalog. `reports.day_data()` gathers everything once and
hands it over, so the SAFE TO FORMAT stamp printed here is quoting the same
`card_status()` result the HTML report quoted, from the same dictionary, in the
same call. There is no second code path that could disagree, because there is
no second query.

reportlab is optional, exactly as NumPy and Pillow are. Without it the PDF is
reported unavailable and everything else — the offload, the verification, the
HTML report, the format gate — is untouched.
"""

import html
import os

import db
import reports
import thumbnails
import version

LAYOUTS = ("table", "metadata", "thumbnail")
DEFAULT_LAYOUT = "table"

SETTING_PRODUCTION = "report_production_name"
SETTING_LOGO = "report_logo_path"

# Matching the HTML report, so the same day does not read as two different
# verdicts depending on which file someone opened.
INK = "#111111"
QUIET = "#666666"
RULE = "#CCCCCC"
FAINT = "#EFEFEF"
GOOD = "#0A7C50"
BAD = "#B3261E"
WARN = "#9A6500"


def _import_reportlab():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, LongTable,
                                    PageTemplate, Paragraph, Spacer, TableStyle)
    return dict(colors=colors, A4=A4, landscape=landscape, ParagraphStyle=ParagraphStyle,
                mm=mm, BaseDocTemplate=BaseDocTemplate, Frame=Frame,
                KeepTogether=KeepTogether, LongTable=LongTable, PageTemplate=PageTemplate,
                Paragraph=Paragraph, Spacer=Spacer, TableStyle=TableStyle)


def availability():
    """(available, reason). The reason is shown to the operator verbatim."""
    try:
        _import_reportlab()
    except Exception as exc:
        return False, (f"reportlab is not installed, so PDF reports are unavailable "
                       f"({exc.__class__.__name__}). The HTML report, the offload and "
                       f"the format gate are unaffected. Install it with: "
                       f"pip install 'reportlab>=4,<5'")
    return True, None


def available():
    return availability()[0]


def branding():
    """Production name and logo, as configured. Never raises."""
    name = (db.setting(SETTING_PRODUCTION) or "").strip()
    logo = (db.setting(SETTING_LOGO) or "").strip()
    logo_error = None
    if logo and not os.path.isfile(logo):
        logo_error = f"The configured logo is not there: {logo}"
        logo = ""
    return {"production": name, "logo": logo, "logo_error": logo_error}


def _image_box(image_path, max_width, max_height):
    """Scaled (width, height) that fits the box, or None if unmeasurable.

    Used for the production logo and for report frames alike; neither is worth
    failing a report over, so an image that cannot be measured is simply not
    drawn.
    """
    try:
        from PIL import Image
        with Image.open(image_path) as image:
            width, height = image.size
    except Exception:
        return None
    if not width or not height:
        return None
    scale = min(max_width / width, max_height / height)
    return width * scale, height * scale


def _styles(rl):
    body = rl["ParagraphStyle"]("shs-body", fontName="Helvetica", fontSize=7.5,
                                leading=9.5, textColor=rl["colors"].HexColor(INK))
    return {
        "body": body,
        "cell": rl["ParagraphStyle"]("shs-cell", parent=body, fontSize=7, leading=8.5),
        "mono": rl["ParagraphStyle"]("shs-mono", parent=body, fontName="Courier",
                                     fontSize=6.5, leading=8),
        "head": rl["ParagraphStyle"]("shs-head", parent=body, fontName="Helvetica-Bold",
                                     fontSize=6.5, leading=8,
                                     textColor=rl["colors"].HexColor(QUIET)),
        "h1": rl["ParagraphStyle"]("shs-h1", parent=body, fontName="Helvetica-Bold",
                                   fontSize=16, leading=19, spaceAfter=2),
        "h2": rl["ParagraphStyle"]("shs-h2", parent=body, fontName="Helvetica-Bold",
                                   fontSize=9, leading=12, spaceBefore=14, spaceAfter=4),
        "sub": rl["ParagraphStyle"]("shs-sub", parent=body, fontSize=7.5, leading=11,
                                    textColor=rl["colors"].HexColor(QUIET)),
        "stamp": rl["ParagraphStyle"]("shs-stamp", parent=body, fontName="Helvetica-Bold",
                                      fontSize=11, leading=14, spaceBefore=2, spaceAfter=2),
    }


def _table(rl, data, widths, aligns=None, cell_colours=None):
    table = rl["LongTable"](data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    colors = rl["colors"]
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(QUIET)),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(RULE)),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor(FAINT)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for column, align in (aligns or {}).items():
        style.append(("ALIGN", (column, 0), (column, -1), align))
    for (column, row), colour in (cell_colours or {}).items():
        style.append(("TEXTCOLOR", (column, row), (column, row), colors.HexColor(colour)))
        style.append(("FONT", (column, row), (column, row), "Helvetica-Bold", 7))
    table.setStyle(rl["TableStyle"](style))
    return table


def _page_furniture(rl, data, brand):
    """Returns the onPage callback: a rule, the provenance line, page numbers."""
    colors = rl["colors"]
    mm = rl["mm"]
    day = data["day"]
    heading = f"{day['project']} · {day['name']} · {day['shoot_date']}"
    if brand["production"]:
        heading = f"{brand['production']} — {heading}"
    provenance = (f"{data['tool']} · GPL-3.0 · {data['support_email']}")

    def draw(canvas, doc):
        canvas.saveState()
        width, height = doc.pagesize

        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(colors.HexColor(QUIET))
        canvas.drawString(doc.leftMargin, height - 12 * mm, heading)
        canvas.setStrokeColor(colors.HexColor(RULE))
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, height - 13.5 * mm,
                    width - doc.rightMargin, height - 13.5 * mm)

        canvas.line(doc.leftMargin, 13 * mm, width - doc.rightMargin, 13 * mm)
        canvas.drawString(doc.leftMargin, 9.5 * mm, provenance)
        canvas.drawRightString(width - doc.rightMargin, 9.5 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return draw


def _cover(rl, styles, data, brand, layout, story, width):
    colors = rl["colors"]
    mm = rl["mm"]
    day = data["day"]

    if brand["logo"]:
        box = _image_box(brand["logo"], 60 * mm, 22 * mm)
        if box:
            from reportlab.platypus import Image as RLImage
            try:
                story.append(RLImage(brand["logo"], width=box[0], height=box[1],
                                     hAlign="LEFT"))
                story.append(rl["Spacer"](1, 6))
            except Exception:
                # A logo is decoration. It never stops the paperwork.
                brand["logo_error"] = "The configured logo could not be drawn."

    if brand["production"]:
        story.append(rl["Paragraph"](_esc(brand["production"]), styles["h1"]))
    story.append(rl["Paragraph"](f"{_esc(day['project'])} &middot; {_esc(day['name'])}",
                                 styles["h1"] if not brand["production"] else styles["h2"]))
    story.append(rl["Paragraph"](
        f"Shoot date {_esc(day['shoot_date'])} &nbsp;·&nbsp; "
        f"report generated {_esc(data['generated'])} &nbsp;·&nbsp; "
        f"{_esc(layout)} layout", styles["sub"]))
    story.append(rl["Spacer"](1, 8))
    story.append(rl["Paragraph"](" &nbsp;·&nbsp; ".join(
        _esc(part) for part in reports.summary_line(data)), styles["sub"]))

    if brand["logo_error"]:
        story.append(rl["Spacer"](1, 4))
        story.append(rl["Paragraph"](
            f"<font color='{WARN}'>{_esc(brand['logo_error'])} "
            f"The report is otherwise complete.</font>", styles["sub"]))

    story.append(rl["Paragraph"]("Destinations", styles["h2"]))
    rows = [["Label", "Role", "Path"]]
    for d in data["destinations"]:
        rows.append([d["label"], d["role"],
                     rl["Paragraph"](_esc(d["path"]), styles["cell"])])
    story.append(_table(rl, rows, [width * 0.18, width * 0.14, width * 0.68]))


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def _card_heading(rl, styles, entry, story):
    c = entry["card"]
    gate = entry["gate"]
    story.append(rl["Paragraph"](
        f"Card {_esc(c['roll'] or c['label'])} &middot; {_esc(c['kind'])}", styles["h2"]))
    label, safe = reports.gate_stamp(gate)
    colour = GOOD if safe else BAD
    story.append(rl["Paragraph"](f"<font color='{colour}'>{label}</font>", styles["stamp"]))
    story.append(rl["Paragraph"](
        f"{_esc(gate['reason'])} &nbsp;·&nbsp; {len(entry['files'])} files "
        f"&nbsp;·&nbsp; {_esc(reports.human_bytes(c['total_bytes']))}", styles["sub"]))
    story.append(rl["Spacer"](1, 4))


def _verification_table(rl, styles, data, entry, story, width):
    """The dense manifest: every file, its hash, and its state on every drive."""
    dests = data["destinations"]
    header = ["File", "Size", "xxHash64"] + [d["label"] for d in dests]
    rows = [header]
    colours = {}
    for index, f in enumerate(entry["files"], start=1):
        row = [rl["Paragraph"](_esc(f["rel_path"]), styles["cell"]),
               reports.human_bytes(f["size"]),
               rl["Paragraph"](_esc(f["src_hash"]), styles["mono"])]
        for column, d in enumerate(dests, start=3):
            state = entry["copy_state"][(f["id"], d["id"])]
            row.append(state)
            if state == "verified":
                colours[(column, index)] = GOOD
            elif state in ("failed", "—"):
                colours[(column, index)] = BAD
        rows.append(row)

    fixed = width * 0.13 + width * 0.16
    per_dest = (width * 0.28) / max(len(dests), 1) if dests else 0
    widths = ([width - fixed - per_dest * len(dests), width * 0.13, width * 0.16]
              + [per_dest] * len(dests))
    story.append(_table(rl, rows, widths, aligns={1: "RIGHT"}, cell_colours=colours))


def _metadata_table(rl, styles, entry, story, width):
    rows = [["Media", "Scene / Take", "Duration", "Start TC", "Format",
             "Audio", "Metadata", "Proxy"]]
    colours = {}
    for index, m in enumerate(entry["media"], start=1):
        scene_take = " / ".join(v for v in (m["scene"], m["take_name"]) if v) or "—"
        if m["notes"]:
            scene_take += f" · {m['notes']}"
        if m["width"] and m["height"]:
            rate = reports.display_rate(m["frame_rate"])
            format_bits = f"{m['width']}×{m['height']}"
            if rate:
                format_bits += f" @ {rate}"
            if m["codec_name"]:
                format_bits += f" · {reports.display_codec(m['codec_name'])}"
        else:
            format_bits = reports.display_codec(m["codec_name"]) or "—"
        audio_bits = []
        if m["sample_rate"]:
            rate = m["sample_rate"]
            audio_bits.append(f"{rate // 1000}kHz" if rate % 1000 == 0 else f"{rate:,}Hz")
        if m["audio_channels"]:
            audio_bits.append(f"{m['audio_channels']}ch")
        if m["bit_depth"]:
            audio_bits.append(f"{m['bit_depth']}b")
        start = m["start_timecode"]
        if not start and m["bwf_time_reference"] is not None:
            start = f"BWF {m['bwf_time_reference']:,} samples"
        meta_status = m["status"] or "not analyzed"
        colours[(6, index)] = (GOOD if meta_status == "ready" else
                               WARN if meta_status == "partial" else BAD)
        if m["category"] == "video":
            proxy_status = m["proxy_status"] or "not built"
            colours[(7, index)] = (GOOD if proxy_status == "ready" else
                                   BAD if proxy_status == "failed" else WARN)
        else:
            proxy_status = "—"
        rows.append([
            rl["Paragraph"](_esc(os.path.basename(m["rel_path"])), styles["cell"]),
            rl["Paragraph"](_esc(scene_take), styles["cell"]),
            reports.human_duration(m["duration_seconds"]),
            start or "—",
            rl["Paragraph"](_esc(format_bits), styles["cell"]),
            " · ".join(audio_bits) or "—",
            meta_status,
            proxy_status,
        ])
    shares = (0.20, 0.15, 0.09, 0.13, 0.16, 0.11, 0.09, 0.07)
    story.append(_table(rl, rows, [width * share for share in shares], cell_colours=colours))


THUMBNAIL_COLUMNS = 4


def _thumbnail_grid(rl, styles, entry, story, width):
    """A page of frames. Everything here is allowed to be missing.

    A clip with no frame gets a labelled empty cell rather than being left out:
    a contact sheet that quietly drops a clip is worse than one that says it
    could not draw it.
    """
    from reportlab.platypus import Image as RLImage

    clips = [m for m in entry["media"] if m.get("category") == "video"]
    if not clips:
        story.append(rl["Paragraph"](
            "No camera clips on this card.", styles["sub"]))
        return

    gutter = 6
    cell = (width - gutter * (THUMBNAIL_COLUMNS - 1)) / THUMBNAIL_COLUMNS
    art = cell - 8

    def cell_flowables(clip):
        block = []
        frame = clip.get("thumbnail")
        drawn = False
        if frame:
            box = _image_box(frame, art, art * 0.75)
            if box:
                try:
                    block.append(RLImage(frame, width=box[0], height=box[1],
                                         hAlign="LEFT"))
                    drawn = True
                except Exception:
                    drawn = False
        if not drawn:
            block.append(rl["Paragraph"](
                f"<font color='{QUIET}'>no frame</font>", styles["cell"]))
        block.append(rl["Spacer"](1, 3))
        block.append(rl["Paragraph"](
            f"<b>{_esc(os.path.basename(clip['rel_path']))}</b>", styles["cell"]))
        scene_take = " / ".join(v for v in (clip.get("scene"), clip.get("take_name"))
                                if v)
        detail = " · ".join(part for part in (
            scene_take,
            reports.human_duration(clip.get("duration_seconds")),
            clip.get("start_timecode") or "",
        ) if part and part != "—")
        if detail:
            block.append(rl["Paragraph"](
                f"<font color='{QUIET}'>{_esc(detail)}</font>", styles["cell"]))
        return block

    rows = []
    for start in range(0, len(clips), THUMBNAIL_COLUMNS):
        chunk = clips[start:start + THUMBNAIL_COLUMNS]
        row = [cell_flowables(clip) for clip in chunk]
        row += [""] * (THUMBNAIL_COLUMNS - len(row))
        rows.append(row)

    table = rl["LongTable"](rows, colWidths=[cell] * THUMBNAIL_COLUMNS,
                            hAlign="LEFT")
    table.setStyle(rl["TableStyle"]([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), gutter),
    ]))
    story.append(table)

    without = sum(1 for clip in clips if not clip.get("thumbnail"))
    if without:
        ok, reason = thumbnails.available()
        story.append(rl["Spacer"](1, 4))
        story.append(rl["Paragraph"](
            f"<font color='{WARN}'>{without} of {len(clips)} clips have no frame. "
            + _esc(reason if not ok else
                   "A frame is only drawn from a verified copy on the primary "
                   "drive — never from the card. Everything else in this report "
                   "is unaffected.")
            + "</font>", styles["sub"]))


def build(day_id, layout=DEFAULT_LAYOUT, out_path=None):
    """Write the day's PDF report. Returns its path.

    Raises RuntimeError when reportlab is missing, and ValueError for an
    unknown layout or a shoot day that does not exist.
    """
    ok, reason = availability()
    if not ok:
        raise RuntimeError(reason)
    if layout not in LAYOUTS:
        raise ValueError(f"Unknown report layout: {layout}")

    # Warm the frame cache before the day is gathered, so day_data() finds the
    # pictures already on disk and never has to decode anything itself. The
    # first build of a big day takes a moment; every one after it is instant.
    if layout == "thumbnail":
        thumbnails.build_for_day(day_id)

    data = reports.day_data(day_id)
    if not data:
        raise ValueError("Unknown shoot day.")

    rl = _import_reportlab()
    styles = _styles(rl)
    mm = rl["mm"]
    pagesize = rl["landscape"](rl["A4"])
    left = right = 14 * mm
    top = 18 * mm
    bottom = 18 * mm
    width = pagesize[0] - left - right

    brand = branding()
    out_path = out_path or os.path.join(
        reports.report_dir(data), f"ingest_{data['day']['name']}_{layout}.pdf")

    doc = rl["BaseDocTemplate"](out_path, pagesize=pagesize,
                                leftMargin=left, rightMargin=right,
                                topMargin=top, bottomMargin=bottom,
                                title=f"{data['day']['project']} {data['day']['name']} "
                                      f"ingest report",
                                author=data["tool"], subject="Verified card offload")
    frame = rl["Frame"](left, bottom, width, pagesize[1] - top - bottom, id="body",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([rl["PageTemplate"](id="day", frames=[frame],
                                             onPage=_page_furniture(rl, data, brand))])

    story = []
    _cover(rl, styles, data, brand, layout, story, width)

    for entry in data["cards"]:
        _card_heading(rl, styles, entry, story)
        if layout == "table":
            _verification_table(rl, styles, data, entry, story, width)
        elif layout == "thumbnail":
            _thumbnail_grid(rl, styles, entry, story, width)
        else:
            if entry["media"]:
                _metadata_table(rl, styles, entry, story, width)
            else:
                story.append(rl["Paragraph"](
                    "No media metadata for this card. Metadata needs ffprobe; the "
                    "offload and its verification do not.", styles["sub"]))

    doc.build(story)
    return out_path


# --- The logo the operator chooses ----------------------------------------
#
# A picked image is copied into the application's own folder rather than
# referenced where it sits. Two reasons, and neither is about convenience:
#
#   * A path into a show folder on an external drive stops resolving the moment
#     that drive is unmounted, which is halfway through every shoot day. A copy
#     does not.
#   * The bytes are never stored as they arrived. They are decoded, checked to
#     be a real image, flattened onto white, resized if enormous, and re-encoded
#     as PNG. Whatever else was in the original file does not survive that, and
#     what reportlab is later handed is a file this program wrote.

MAX_LOGO_BYTES = 12 * 1024 * 1024
MAX_LOGO_EDGE = 2400


def logo_store_path():
    folder = os.path.join(os.path.dirname(db.DB_PATH), "branding")
    return os.path.join(folder, "logo.png")


def store_logo(raw):
    """Sanitise and store a chosen logo. Returns its path.

    `raw` is the decoded bytes of the file the operator picked. Raises
    ValueError with something worth reading for anything that is not a usable
    image.
    """
    if not raw:
        raise ValueError("The chosen file was empty.")
    if len(raw) > MAX_LOGO_BYTES:
        raise ValueError(f"That image is {len(raw) // (1024 * 1024)} MB. "
                         f"The limit is {MAX_LOGO_BYTES // (1024 * 1024)} MB — "
                         f"a report logo does not need to be larger.")
    try:
        from PIL import Image
    except Exception:
        raise ValueError("Pillow is not installed, so a picked image cannot be "
                         "checked and converted. Type a path to a PNG instead, "
                         "or install Pillow.")

    import io
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()          # rejects anything that is not really an image
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if max(image.size) > MAX_LOGO_EDGE:
                scale = MAX_LOGO_EDGE / max(image.size)
                image = image.resize((max(1, round(image.size[0] * scale)),
                                      max(1, round(image.size[1] * scale))))
            converted = image.convert("RGBA")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"That file could not be read as an image "
                         f"({exc.__class__.__name__}). PNG, JPEG, TIFF and HEIC "
                         f"all work.")

    path = logo_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"
    converted.save(partial, format="PNG", optimize=True)
    os.replace(partial, path)
    db.setting(SETTING_LOGO, path)
    return path


def clear_logo():
    """Forget the logo. The stored copy goes; nothing of the operator's does."""
    db.setting(SETTING_LOGO, "")
    stored = logo_store_path()
    if os.path.isfile(stored):
        try:
            os.remove(stored)
        except OSError:
            pass
