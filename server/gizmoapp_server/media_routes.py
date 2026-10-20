"""Short browser requests backed by CW's authenticated, durable media jobs."""
from flask import Response, jsonify, request
from .config import scoped_path
from .media import CourseMediaError, PendingMedia, generate_image, synthesize_speech, poll_media


def register_media_routes(app):
    prefix = app.config["URL_PREFIX"]

    @app.post(scoped_path(prefix, "api/course-media/<operation>"))
    def course_media(operation):
        if request.content_length is None or request.content_length > 16384:
            return jsonify(errors=["Media request is too large or has no length."]), 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(errors=["Expected a JSON object."]), 400
        try:
            if operation == "image":
                if not isinstance(payload.get("model", "lcm-sd15"), str):
                    return jsonify(errors=["model must be a string."]), 400
                result = generate_image(payload.get("prompt", ""),
                    model=payload.get("model", "lcm-sd15"), wait=False)
            elif operation == "speech":
                result = synthesize_speech(payload.get("text", ""),
                    voice=payload.get("voice", "af_heart"), language=payload.get("language", "a"),
                    speed=payload.get("speed", 1.0), wait=False)
            elif operation == "poll":
                result = poll_media(PendingMedia(payload.get("pollTicket", ""), "pending"))
            else:
                return jsonify(errors=["Unknown media operation."]), 404
        except CourseMediaError as exc:
            return jsonify(errors=[str(exc)]), exc.status
        if isinstance(result, PendingMedia):
            response = jsonify(pollTicket=result.poll_ticket, status=result.status)
            response.status_code = 202
            response.headers["Retry-After"] = "2"
        else:
            response = Response(result.data, content_type=result.content_type)
        response.headers["Cache-Control"] = "no-store"
        return response
