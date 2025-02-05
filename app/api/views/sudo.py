from flask import jsonify, g, request
from sqlalchemy_utils.types.arrow import arrow

from app.api.base import api_bp, require_api_auth
from app.db import Session
from app.log import LOG


def validate_password(user, password):
    """Helper function to check user password"""
    if not password:
        return False
    return user.check_password(password)


@api_bp.route("/sudo", methods=["PATCH"])
@require_api_auth
def enter_sudo():
    """
    Enter sudo mode for elevated permissions.

    Input:
    - password: user's current password (required)

    Output:
    - ok: True if sudo mode is successfully activated
    - error: Error message with 403 status if validation fails
    """
    user = g.user
    data = request.get_json() or {}

    # Validate password input
    password = data.get("password")
    if not validate_password(user, password):
        LOG.warning("Failed sudo mode entry attempt for user %s", user.email)
        return jsonify(error="Invalid password or missing password"), 403

    # Update sudo_mode_at to current time
    g.api_key.sudo_mode_at = arrow.now()
    Session.commit()

    LOG.info("Sudo mode activated for user %s", user.email)
    return jsonify(ok=True), 200
