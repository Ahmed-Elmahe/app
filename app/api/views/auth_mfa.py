import pyotp
from flask import jsonify, request
from flask_login import login_user
from itsdangerous import Signer, BadSignature

from app.api.base import api_bp
from app.config import FLASK_SECRET
from app.db import Session
from app.email_utils import send_invalid_totp_login_email
from app.extensions import limiter
from app.log import LOG
from app.models import User, ApiKey


@api_bp.route("/auth/mfa", methods=["POST"])
@limiter.limit("10/minute")
def auth_mfa():
    """
    Validate the OTP Token
    Input:
        mfa_token: OTP token that user enters
        mfa_key: MFA key obtained in previous auth request, e.g. /api/auth/login
        device: the device name, used to create an ApiKey associated with this device
    Output:
        200 and user info containing:
        {
            name: "John Wick",
            api_key: "a long string",
            email: "user email"
        }
    """
    data = request.get_json()
    if not data:
        return jsonify(error="Request body cannot be empty"), 400

    mfa_token = data.get("mfa_token")
    mfa_key = data.get("mfa_key")
    device = data.get("device")

    # Validate required fields
    if not all([mfa_token, mfa_key, device]):
        return jsonify(error="Missing required fields"), 400

    s = Signer(FLASK_SECRET)
    try:
        user_id = int(s.unsign(mfa_key))
    except (BadSignature, ValueError):
        # Handle invalid or tampered mfa_key
        return jsonify(error="Invalid mfa_key"), 400

    user = User.get(user_id)
    if not user:
        return jsonify(error="Invalid mfa_key"), 400
    elif not user.enable_otp:
        return (
            jsonify(error="This endpoint should only be used by users who enable MFA"),
            400,
        )

    # Verify the TOTP token
    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(mfa_token, valid_window=2):
        send_invalid_totp_login_email(user, "TOTP")
        return jsonify(error="Wrong TOTP Token"), 400

    # Prepare the response data
    ret = {"name": user.name or "", "email": user.email}

    # Check if an API key already exists for the device
    api_key = ApiKey.get_by(user_id=user.id, name=device)
    if not api_key:
        LOG.d("Create new API key for %s and %s", user, device)
        api_key = ApiKey.create(user.id, device)
        Session.commit()

    ret["api_key"] = api_key.code

    # Log the user in automatically on the web
    login_user(user)

    return jsonify(**ret), 200
