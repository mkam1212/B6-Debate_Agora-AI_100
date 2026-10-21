"""Durable, capability-addressed chat uploads; originals never enter the public static tree."""
from __future__ import annotations

import base64
import hmac
import re
import secrets
import time
import warnings
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile, BadZipFile

from flask import current_app, jsonify, request, send_file, session
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from werkzeug.exceptions import RequestEntityTooLarge

from .account_api import _rate_limit, current_user
from .config import scoped_path
from .db import WRITE_LOCK, get_db

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 6
MAX_DOCUMENTS = 6
MAX_TEXT = 16_000
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
DOCUMENT_TYPES = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
ID_RE = re.compile(r"^[a-f0-9]{32}$")
ORPHAN_RETENTION_SECONDS = 24 * 3600


def cleanup_attachments():
    """Only abandoned uploads expire; saved messages, queues and drafts are retained."""
    connection = get_db()
    with WRITE_LOCK:
        deleted = connection.execute(
            'DELETE FROM debate_attachments WHERE created_at<? AND NOT EXISTS '
            '(SELECT 1 FROM debate_attachment_links l WHERE l.attachment_id=debate_attachments.id)',
            (int(time.time()) - ORPHAN_RETENTION_SECONDS,),
        ).rowcount
        connection.commit()
    return deleted


def metadata(row):
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "mime": row["mime"], "size": row["byte_size"]}


def owns_attachment(row):
    user = current_user()
    if row["owner_id"] == session.get("upload_owner") or (user and row["owner_id"] == user["id"]):
        return True
    return bool(user and get_db().execute(
        "SELECT 1 FROM debate_attachment_links l JOIN debate_sessions s ON s.id=l.session_id "
        "WHERE l.attachment_id=? AND s.owner_id=?", (row["id"], user["id"]),
    ).fetchone())


def normalize_attachments(raw):
    if not isinstance(raw, list) or len(raw) > MAX_IMAGES + MAX_DOCUMENTS:
        raise ValueError("Attach up to 6 images and 6 documents per message.")
    result, seen = [], set()
    for item in raw:
        attachment_id = item.get("id") if isinstance(item, dict) else item
        if not isinstance(attachment_id, str) or not ID_RE.fullmatch(attachment_id) or attachment_id in seen:
            raise ValueError("Invalid or duplicate attachment.")
        row = get_db().execute("SELECT id,name,kind,mime,byte_size,owner_id FROM debate_attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None or not owns_attachment(row):
            raise ValueError("An attachment is no longer available. Upload it again.")
        result.append(metadata(row)); seen.add(attachment_id)
    if sum(item["kind"] == "image" for item in result) > MAX_IMAGES or sum(item["kind"] == "file" for item in result) > MAX_DOCUMENTS:
        raise ValueError("Attach up to 6 images and 6 documents per message.")
    return result


def link_attachments(connection, saved):
    attachments = list(saved.get("draftAttachments", []))
    for message in [*saved.get("messages", []), *saved.get("pendingUserMessages", [])]:
        attachments.extend(message.get("attachments", []))
    with WRITE_LOCK:
        connection.execute('DELETE FROM debate_attachment_links WHERE session_id=?', (saved['id'],))
        connection.executemany("INSERT OR IGNORE INTO debate_attachment_links VALUES (?,?)", [(item["id"], saved["id"]) for item in attachments])
        connection.commit()


def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > 12 * 1024 * 1024:
        raise ValueError("This document is too complex to preview.")
    content = archive.read(info)
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("Unsupported document structure.")
    return ElementTree.fromstring(content)


def _office_text(data, extension):
    with ZipFile(BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) > 2048 or sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
            raise ValueError("This document is too complex to preview.")
        if extension == ".docx":
            root = _xml(archive, "word/document.xml")
            return "\n".join("".join(node.itertext()) for node in root.iter() if node.tag.endswith("}t"))[:MAX_TEXT]
        if extension == ".pptx":
            slides = sorted((name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)), key=lambda name: int(re.search(r"slide(\d+)\.xml", name).group(1)))
            chunks = []
            for name in slides:
                chunks.append("\n".join(node.text or "" for node in _xml(archive, name).iter() if node.tag.endswith("}t")))
                if sum(map(len, chunks)) >= MAX_TEXT: break
            return "\n\n".join(chunks)[:MAX_TEXT]
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(node.itertext()) for node in _xml(archive, "xl/sharedStrings.xml") if node.tag.endswith("}si")]
        chunks = []
        sheets = sorted(name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
        for name in sheets:
            for row in _xml(archive, name).iter():
                if not row.tag.endswith("}row"): continue
                cells = []
                for cell in row:
                    value = "".join(node.text or "" for node in cell.iter() if node.tag.endswith(("}v", "}t")))
                    if cell.attrib.get("t") == "s" and value.isdigit():
                        value = shared[int(value)] if int(value) < len(shared) else ""
                    cells.append(value)
                chunks.append("\t".join(cells))
                if sum(map(len, chunks)) >= MAX_TEXT: return "\n".join(chunks)[:MAX_TEXT]
        return "\n".join(chunks)[:MAX_TEXT]


def _document_text(data, extension):
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"): raise ValueError("This is not a valid PDF file.")
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted: return "", "encrypted"
            chunks = []
            for page in reader.pages[:60]:
                chunks.append(page.extract_text() or "")
                if sum(map(len, chunks)) >= MAX_TEXT: break
            text = "\n".join(chunks)[:MAX_TEXT]
            return text, "ready" if text.strip() else "no-text"
        except Exception:
            return "", "no-text"
    if extension in {".docx", ".xlsx", ".pptx"}:
        try: return _office_text(data, extension), "ready"
        except (BadZipFile, KeyError, ElementTree.ParseError): raise ValueError("This Office file could not be read.") from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try: text = data.decode("utf-16")
        except UnicodeError: raise ValueError("Save text files using UTF-8 or UTF-16.") from None
    if "\x00" in text: raise ValueError("This is not a readable text file.")
    return text[:MAX_TEXT], "ready"


def _image_preview(data, extension):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                actual = Image.MIME.get(source.format)
                if actual != IMAGE_TYPES[extension] or source.width * source.height > 20_000_000:
                    raise ValueError("Use a PNG, JPEG, WebP, or GIF image under 20 megapixels.")
                source.verify()
            with Image.open(BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((1536, 1536))
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                output = BytesIO(); background.save(output, format="JPEG", quality=88)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("This image could not be read. Try another image.") from None


def prepare_attachment_context(conversation):
    """Bound context size; include the latest six images without logging binary content."""
    prepared, image_count, text_budget = {}, 0, 24_000
    for turn in reversed(conversation):
        for item in reversed(turn.get("attachments", [])):
            attachment_id = item["id"]
            if attachment_id in prepared: continue
            row = get_db().execute("SELECT name,kind,preview,extracted_text,text_status FROM debate_attachments WHERE id=?", (attachment_id,)).fetchone()
            if row is None: continue
            text = f"\n\nAttached {row['kind']}: {row['name']}\n"
            image_part = None
            if row["kind"] == "image":
                if image_count < MAX_IMAGES:
                    image_count += 1
                    image_part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(row["preview"]).decode(), "detail": "auto"}}
                else: text += "Earlier image omitted from this request; do not claim to see it."
            else:
                excerpt = row["extracted_text"][:text_budget]
                text_budget -= len(excerpt)
                text += (f"Reference text (may be an excerpt):\n{excerpt}" if excerpt else "No readable text is available; do not invent this file's contents.")
            prepared[attachment_id] = (text, image_part)
    return prepared


def register_attachment_routes(app):
    prefix = app.config["URL_PREFIX"]
    last_cleanup = 0

    @app.before_request
    def collect_abandoned_uploads():
        nonlocal last_cleanup
        now = time.monotonic()
        if now - last_cleanup >= 3600:
            cleanup_attachments()
            last_cleanup = now

    @app.delete(scoped_path(prefix, 'api/debate/attachments/<attachment_id>'))
    def discard_attachment(attachment_id):
        csrf, supplied = session.get('csrf'), request.headers.get('X-CSRF-Token', '')
        if not isinstance(csrf, str) or not csrf or not hmac.compare_digest(csrf.encode(), supplied.encode()):
            return jsonify(errors=['Refresh this page and try again.']), 403
        db = get_db()
        with WRITE_LOCK:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM debate_attachments WHERE id=?', (attachment_id,)).fetchone() if ID_RE.fullmatch(attachment_id) else None
            if row and not owns_attachment(row):
                db.rollback()
                return jsonify(errors=['Attachment not found.']), 404
            # Never remove a file still referenced by a saved message or another draft.
            db.execute('DELETE FROM debate_attachments WHERE id=? AND NOT EXISTS '
                       '(SELECT 1 FROM debate_attachment_links WHERE attachment_id=?)', (attachment_id, attachment_id))
            db.commit()
        return '', 204

    @app.post(scoped_path(prefix, "api/debate/attachments"))
    def upload_attachment():
        csrf, supplied = session.get("csrf"), request.headers.get("X-CSRF-Token", "")
        if not isinstance(csrf, str) or not csrf or not hmac.compare_digest(csrf.encode(), supplied.encode()):
            return jsonify(errors=["Refresh this page and try uploading again."]), 403
        _rate_limit(f"uploads:{request.remote_addr}", 120, 3600)
        request.max_content_length = MAX_FILE_BYTES + 65_536
        try:
            files = request.files.getlist("file")
            if len(files) != 1: return jsonify(errors=["Choose one file per upload request."]), 400
            file = files[0]
            name = Path((file.filename or "").replace("\\", "/")).name
            name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:180]
            extension = Path(name).suffix.lower()
            if extension not in IMAGE_TYPES and extension not in DOCUMENT_TYPES:
                raise ValueError("Use PNG, JPEG, WebP, GIF, PDF, TXT, MD, CSV, JSON, DOCX, XLSX, or PPTX.")
            data = file.stream.read(MAX_FILE_BYTES + 1)
            if not data: raise ValueError("This file is empty.")
            if len(data) > MAX_FILE_BYTES: return jsonify(errors=["Each attachment must be 20 MB or smaller."]), 413
            kind = "image" if extension in IMAGE_TYPES else "file"
            mime = {**IMAGE_TYPES, **DOCUMENT_TYPES}[extension]
            preview, text, status = None, "", ""
            if kind == "image": preview = _image_preview(data, extension)
            else: text, status = _document_text(data, extension)
        except RequestEntityTooLarge:
            return jsonify(errors=["Each attachment must be 20 MB or smaller."]), 413
        except ValueError as error:
            return jsonify(errors=[str(error)]), 400
        user = current_user()
        owner = user["id"] if user else session.setdefault("upload_owner", secrets.token_hex(16))
        attachment_id = secrets.token_hex(16)
        db = get_db()
        with WRITE_LOCK:
            used = db.execute("SELECT COALESCE(SUM(byte_size),0) FROM debate_attachments WHERE owner_id=?", (owner,)).fetchone()[0]
            if used + len(data) > 256 * 1024 * 1024:
                db.commit()
                return jsonify(errors=["Your upload storage is full. Try smaller files."]), 413
            db.execute("INSERT INTO debate_attachments VALUES (?,?,?,?,?,?,?,?,?,?,?)", (attachment_id, owner, name, kind, mime, len(data), data, preview, text, status, int(time.time())))
            db.commit()
            row = db.execute("SELECT id,name,kind,mime,byte_size FROM debate_attachments WHERE id=?", (attachment_id,)).fetchone()
        return jsonify(attachment=metadata(row)), 201

    @app.get(scoped_path(prefix, "api/debate/attachments/<attachment_id>/<operation>"))
    def attachment_content(attachment_id, operation):
        if not ID_RE.fullmatch(attachment_id) or operation not in {"content", "preview"}:
            return jsonify(errors=["Attachment not found."]), 404
        row = get_db().execute("SELECT * FROM debate_attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None: return jsonify(errors=["Attachment not found."]), 404
        if not owns_attachment(row):
            from .session_access import require_session
            saved, access = require_session(request.args.get("session", ""))
            visible = any(item.get("id") == attachment_id for message in saved["messages"] for item in message.get("attachments", []))
            if access == "owner" or not visible:
                return jsonify(errors=["Attachment not found."]), 404
        if operation == "preview" and row["kind"] == "file":
            response = jsonify(text=row["extracted_text"], status=row["text_status"])
        else:
            is_preview = operation == "preview"
            response = send_file(BytesIO(row["preview"] if is_preview else row["original"]), mimetype="image/jpeg" if is_preview else row["mime"], as_attachment=not is_preview, download_name=row["name"], max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
