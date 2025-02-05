import base64
import dataclasses
from io import BytesIO
from typing import Optional

from flask import jsonify, g, request, make_response

from app import s3, config
from app.api.base import api_bp, require_api_auth
from app.config import SESSION_COOKIE_NAME
from app.dashboard.views.index import get_stats
from app.db import Session
from app.image_validation import detect_image_format, ImageFormat
from app.models import ApiKey, File, PartnerUser, User
from app.proton.proton_partner import get_proton_partner
from app.session import logout_session
from app.utils import random_string
from app.log import LOG


def get_connected_proton_address(user: User) -> Optional[str]:
    """Retrieve the user's connected ProtonMail address if available."""
    proton_partner = get_proton_partner()
    partner_user = PartnerUser.get_by(user_id=user.id, partner_id=proton_partner.id)
    return partner_user.partner_email if partner_user else None


def user_to_dict(user: User) -> dict:
    """Convert User object to a dictionary for JSON response."""
    return {
        "name": user.name or "",
        "is_premium": user.is_premium(),
        "email": user.email,
        "in_trial": user.in_trial(),
        "max_alias_free_plan": user.max_alias_for_free_account(),
        "connected_proton_address": get_connected_proton_address(user) if config.CONNECT_WITH_PROTON else None,
        "can_create_reverse_alias": user.can_create_contacts(),
        "profile_picture_url": user.profile_picture.get_url() if user.profile_picture_id else None,
    }


def update_profile_picture(user: User, new_picture: Optional[str]):
    """Helper function to handle profile picture updates."""
    # Remove existing picture if set to null
    if user.profile_picture_id:
        file = user.profile_picture
        user.profile_picture_id = None
        Session.flush()
        if file:
            File.delete(file.id)
            s3.delete(file.path)
            LOG.info("Profile picture deleted for user %s", user.email)
        Session.flush()

    # Set new profile picture if provided
    if new_picture is not None:
        raw_data = base64.decodebytes(new_picture.encode())
        if detect_image_format(raw_data) == ImageFormat.Unknown:
            return jsonify(error="Unsupported image format"), 400
        file_path = random_string(30)
        file = File.create(user_id=user.id, path=file_path)
        Session.flush()
        s3.upload_from_bytesio(file_path, BytesIO(raw_data))
        user.profile_picture_id = file.id
        LOG.info("Profile picture updated for user %s", user.email)
        Session.flush()


@api_bp.route("/user_info")
@require_api_auth
def user_info():
    """Return user info given the API key."""
    return jsonify(user_to_dict(g.user))


@api_bp.route("/user_info", methods=["PATCH"])
@require_api_auth
def update_user_info():
    """
    Update user info.
    Input:
    - profile_picture (optional): base64 of the profile picture. Set to null to remove it.
    - name (optional)
    """
    user = g.user
    data = request.get_json() or {}

    # Update profile picture if provided
    if "profile_picture" in data:
        profile_update_response = update_profile_picture(user, data["profile_picture"])
        if profile_update_response:
            return profile_update_response

    # Update name if provided
    if "name" in data:
        user.name = data["name"]

    Session.commit()

    LOG.info("User info updated for %s", user.email)
    return jsonify(user_to_dict(user))


@api_bp.route("/api_key", methods=["POST"])
@require_api_auth
def create_api_key():
    """Create a new API key."""
    data = request.get_json()
    if not data or "device" not in data:
        return jsonify(error="Device name is required"), 400

    api_key = ApiKey.create(user_id=g.user.id, name=data["device"])
    Session.commit()

    LOG.info("API key created for user %s on device %s", g.user.email, data["device"])
    return jsonify(api_key=api_key.code), 201


@api_bp.route("/logout", methods=["GET"])
@require_api_auth
def logout():
    """Log the user out on the web, removing the session cookie."""
    logout_session()
    response = make_response(jsonify(msg="User is logged out"), 200)
    response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, secure=True)

    LOG.info("User %s logged out", g.user.email)
    return response


@api_bp.route("/stats")
@require_api_auth
def user_stats():
    """Return user statistics such as alias count, forwards, replies, and blocks."""
    stats = get_stats(g.user)
    return jsonify(dataclasses.asdict(stats))
