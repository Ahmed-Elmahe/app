from functools import wraps
from typing import Optional, Tuple

import arrow
from flask import Blueprint, jsonify, g, request
from flask_login import current_user

from app.db import Session
from app.models import ApiKey

api_bp = Blueprint("api", __name__, url_prefix="/api")
SUDO_MODE_VALID = 5  # minutes


def authorize_request() -> Optional[Tuple[str, int]]:
    if not (api_key := ApiKey.get_by(code=request.headers.get("Authentication"))):
        if not current_user.is_authenticated:
            return jsonify(error="Wrong API key"), 401
        g.user = current_user
    else:
        api_key.update(last_used=arrow.now(), times=api_key.times + 1)
        Session.commit()
        g.user = api_key.user

    if g.user.disabled:
        return jsonify(error="Disabled account"), 403
    if not g.user.is_active():
        return jsonify(error="Account inactive"), 401

    g.api_key = api_key  # None for session auth
    return None


def check_sudo(api_key: Optional[ApiKey]) -> bool:
    return bool(api_key and api_key.sudo_mode_at and
                api_key.sudo_mode_at >= arrow.now().shift(minutes=-SUDO_MODE_VALID))


def require_api_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        return error if (error := authorize_request()) else f(*args, **kwargs)
    return decorated


def require_api_sudo(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if error := authorize_request():
            return error
        return f(*args, **kwargs) if check_sudo(g.api_key) else (jsonify(error="Sudo required"), 440)
    return decorated





