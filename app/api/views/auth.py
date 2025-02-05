import secrets
import string

import facebook
import google.oauth2.credentials
import googleapiclient.discovery
from flask import jsonify, request
from flask_login import login_user
from itsdangerous import Signer

from app import email_utils
from app.api.base import api_bp
from app.config import FLASK_SECRET, DISABLE_REGISTRATION
from app.dashboard.views.account_setting import send_reset_password_email
from app.db import Session
from app.email_utils import (
    email_can_be_used_as_mailbox,
    personal_email_already_used,
    send_email,
    render,
)
from app.events.auth_event import LoginEvent, RegisterEvent
from app.extensions import limiter
from app.log import LOG
from app.models import User, ApiKey, SocialAuth, AccountActivation
from app.user_audit_log_utils import emit_user_audit_log, UserAuditLogAction
from app.utils import sanitize_email, canonicalize_email


@api_bp.route("/auth/login", methods=["POST"])
@limiter.limit("10/minute")
def auth_login():
    """
    Authenticate user
    Input:
        email: User's email address
        password: User's password
        device: Device name to create an ApiKey associated with this device
    Output:
        200 and user info containing:
        {
            name: "John Wick",
            mfa_enabled: true,
            mfa_key: "a long string",
            api_key: "a long string"
        }
    """
    data = request.get_json()
    if not data:
        return jsonify(error="Request body cannot be empty"), 400

    # Validate required fields
    email = data.get("email")
    password = data.get("password")
    device = data.get("device")

    if not all([email, password, device]):
        LoginEvent(LoginEvent.ActionType.failed, LoginEvent.Source.api).send()
        return jsonify(error="Missing required fields"), 400

    # Sanitize and canonicalize email
    email = sanitize_email(email)
    canonical_email = canonicalize_email(email)

    # Fetch user by email or canonical email
    user = User.get_by(email=email) or User.get_by(email=canonical_email)

    # Validate user and password
    if not user or not user.check_password(password):
        LoginEvent(LoginEvent.ActionType.failed, LoginEvent.Source.api).send()
        return jsonify(error="Email or password incorrect"), 400

    # Check account status
    if user.disabled:
        LoginEvent(LoginEvent.ActionType.disabled_login, LoginEvent.Source.api).send()
        return jsonify(error="Account disabled"), 400
    elif user.delete_on is not None:
        LoginEvent(LoginEvent.ActionType.scheduled_to_be_deleted, LoginEvent.Source.api).send()
        return jsonify(error="Account scheduled for deletion"), 400
    elif not user.activated:
        LoginEvent(LoginEvent.ActionType.not_activated, LoginEvent.Source.api).send()
        return jsonify(error="Account not activated"), 403  # Changed to 403 for semantic correctness

    # Handle FIDO and TOTP
    if user.fido_enabled() and not user.enable_otp:
        return jsonify(error="FIDO authentication is not supported for this application"), 403

    # Log successful login
    LoginEvent(LoginEvent.ActionType.success, LoginEvent.Source.api).send()

    # Return authentication payload
    return jsonify(**auth_payload(user, device)), 200


@api_bp.route("/auth/register", methods=["POST"])
@limiter.limit("10/minute")
def auth_register():
    """
    User signs up - will need to activate their account with an activation code.
    Input:
        email: User's email address
        password: User's password
    Output:
        200: user needs to confirm their account
    """
    data = request.get_json()
    if not data:
        return jsonify(error="Request body cannot be empty"), 400

    # Validate required fields
    dirty_email = data.get("email")
    password = data.get("password")

    if not all([dirty_email, password]):
        RegisterEvent(RegisterEvent.ActionType.failed, RegisterEvent.Source.api).send()
        return jsonify(error="Missing required fields"), 400

    # Canonicalize email
    email = canonicalize_email(dirty_email)

    # Check if registration is disabled
    if DISABLE_REGISTRATION:
        RegisterEvent(RegisterEvent.ActionType.failed, RegisterEvent.Source.api).send()
        return jsonify(error="Registration is closed"), 403  # Changed to 403 for semantic correctness

    # Validate email
    if not email_can_be_used_as_mailbox(email) or personal_email_already_used(email):
        RegisterEvent(RegisterEvent.ActionType.invalid_email, RegisterEvent.Source.api).send()
        return jsonify(error=f"Cannot use {email} as personal inbox"), 400

    # Validate password
    if len(password) < 8:
        RegisterEvent(RegisterEvent.ActionType.failed, RegisterEvent.Source.api).send()
        return jsonify(error="Password must be at least 8 characters long"), 400
    if len(password) > 100:
        RegisterEvent(RegisterEvent.ActionType.failed, RegisterEvent.Source.api).send()
        return jsonify(error="Password must be at most 100 characters long"), 400

    # Create user
    LOG.d("Create user %s", email)
    user = User.create(email=email, name=dirty_email, password=password)
    Session.flush()

    # Generate activation code
    code = "".join([str(secrets.choice(string.digits)) for _ in range(6)])
    AccountActivation.create(user_id=user.id, code=code)
    Session.commit()

    # Send activation email
    send_email(
        email,
        "Just one more step to join SimpleLogin",
        render("transactional/code-activation.txt.jinja2", user=user, code=code),
        render("transactional/code-activation.html", user=user, code=code),
    )

    # Log successful registration
    RegisterEvent(RegisterEvent.ActionType.success, RegisterEvent.Source.api).send()

    return jsonify(msg="User needs to confirm their account"), 200


@api_bp.route("/auth/activate", methods=["POST"])
@limiter.limit("10/minute")
def auth_activate():
    """
    User enters the activation code to confirm their account.
    Input:
        email: User's email address
        code: Activation code
    Output:
        200: user account is now activated, user can login now
        400: wrong email, code
        410: wrong code too many times
    """
    data = request.get_json()
    if not data:
        return jsonify(error="Request body cannot be empty"), 400

    # Validate required fields
    email = data.get("email")
    code = data.get("code")

    if not all([email, code]):
        return jsonify(error="Missing required fields"), 400

    # Sanitize and canonicalize email
    email = sanitize_email(email)
    canonical_email = canonicalize_email(email)

    # Fetch user by email or canonical email
    user = User.get_by(email=email) or User.get_by(email=canonical_email)

    # Do not use a different message to avoid exposing existing email
    if not user or user.activated:
        return jsonify(error="Wrong email or code"), 400

    # Fetch account activation record
    account_activation = AccountActivation.get_by(user_id=user.id)
    if not account_activation:
        return jsonify(error="Wrong email or code"), 400

    # Validate activation code
    if account_activation.code != code:
        # Decrement number of tries
        account_activation.tries -= 1
        Session.commit()

        # Check if too many wrong tries
        if account_activation.tries == 0:
            AccountActivation.delete(account_activation.id)
            Session.commit()
            return jsonify(error="Too many wrong tries"), 410

        return jsonify(error="Wrong email or code"), 400

    # Activate user account
    LOG.d("Activate user %s", user)
    user.activated = True
    emit_user_audit_log(
        user=user,
        action=UserAuditLogAction.ActivateUser,
        message=f"User has been activated: {user.email}",
    )
    AccountActivation.delete(account_activation.id)
    Session.commit()

    return jsonify(msg="Account is activated, user can login now"), 200


@api_bp.route("/auth/reactivate", methods=["POST"])
@limiter.limit("10/minute")
def auth_reactivate():
    """
    User asks for another activation code
    Input:
        email
    Output:
        200: user is going to receive an email for activate their account

    """
    data = request.get_json()
    if not data:
        return jsonify(error="request body cannot be empty"), 400

    email = sanitize_email(data.get("email"))
    canonical_email = canonicalize_email(data.get("email"))

    user = User.get_by(email=email) or User.get_by(email=canonical_email)

    # do not use a different message to avoid exposing existing email
    if not user or user.activated:
        return jsonify(error="Something went wrong"), 400

    account_activation = AccountActivation.get_by(user_id=user.id)
    if account_activation:
        AccountActivation.delete(account_activation.id)
        Session.commit()

    # create activation code
    code = "".join([str(secrets.choice(string.digits)) for _ in range(6)])
    AccountActivation.create(user_id=user.id, code=code)
    Session.commit()

    send_email(
        email,
        "Just one more step to join SimpleLogin",
        render("transactional/code-activation.txt.jinja2", user=user, code=code),
        render("transactional/code-activation.html", user=user, code=code),
    )

    return jsonify(msg="User needs to confirm their account"), 200


@api_bp.route("/auth/facebook", methods=["POST"])
@limiter.limit("10/minute")
def auth_facebook():
    """
    Authenticate user with Facebook
    Input:
        facebook_token: facebook access token
        device: to create an ApiKey associated with this device
    Output:
        200 and user info containing:
        {
            name: "John Wick",
            mfa_enabled: true,
            mfa_key: "a long string",
            api_key: "a long string"
        }

    """
    data = request.get_json()
    if not data:
        return jsonify(error="request body cannot be empty"), 400

    facebook_token = data.get("facebook_token")
    device = data.get("device")

    graph = facebook.GraphAPI(access_token=facebook_token)
    user_info = graph.get_object("me", fields="email,name")
    email = sanitize_email(user_info.get("email"))

    user = User.get_by(email=email)

    if not user:
        if DISABLE_REGISTRATION:
            return jsonify(error="registration is closed"), 400
        if not email_can_be_used_as_mailbox(email) or personal_email_already_used(
            email
        ):
            return jsonify(error=f"cannot use {email} as personal inbox"), 400

        LOG.d("create facebook user with %s", user_info)
        user = User.create(email=email, name=user_info["name"], activated=True)
        Session.commit()
        email_utils.send_welcome_email(user)

    if not SocialAuth.get_by(user_id=user.id, social="facebook"):
        SocialAuth.create(user_id=user.id, social="facebook")
        Session.commit()

    return jsonify(**auth_payload(user, device)), 200


@api_bp.route("/auth/google", methods=["POST"])
@limiter.limit("10/minute")
def auth_google():
    """
    Authenticate user with Google
    Input:
        google_token: Google access token
        device: to create an ApiKey associated with this device
    Output:
        200 and user info containing:
        {
            name: "John Wick",
            mfa_enabled: true,
            mfa_key: "a long string",
            api_key: "a long string"
        }

    """
    data = request.get_json()
    if not data:
        return jsonify(error="request body cannot be empty"), 400

    google_token = data.get("google_token")
    device = data.get("device")

    cred = google.oauth2.credentials.Credentials(token=google_token)

    build = googleapiclient.discovery.build("oauth2", "v2", credentials=cred)

    user_info = build.userinfo().get().execute()
    email = sanitize_email(user_info.get("email"))

    user = User.get_by(email=email)

    if not user:
        if DISABLE_REGISTRATION:
            return jsonify(error="registration is closed"), 400
        if not email_can_be_used_as_mailbox(email) or personal_email_already_used(
            email
        ):
            return jsonify(error=f"cannot use {email} as personal inbox"), 400

        LOG.d("create Google user with %s", user_info)
        user = User.create(email=email, name="", activated=True)
        Session.commit()
        email_utils.send_welcome_email(user)

    if not SocialAuth.get_by(user_id=user.id, social="google"):
        SocialAuth.create(user_id=user.id, social="google")
        Session.commit()

    return jsonify(**auth_payload(user, device)), 200


def auth_payload(user, device) -> dict:
    ret = {"name": user.name or "", "email": user.email, "mfa_enabled": user.enable_otp}

    # do not give api_key, user can only obtain api_key after OTP verification
    if user.enable_otp:
        s = Signer(FLASK_SECRET)
        ret["mfa_key"] = s.sign(str(user.id))
        ret["api_key"] = None
    else:
        api_key = ApiKey.get_by(user_id=user.id, name=device)
        if not api_key:
            LOG.d("create new api key for %s and %s", user, device)
            api_key = ApiKey.create(user.id, device)
            Session.commit()
        ret["mfa_key"] = None
        ret["api_key"] = api_key.code

        # so user is automatically logged in on the web
        login_user(user)

    return ret


@api_bp.route("/auth/forgot_password", methods=["POST"])
@limiter.limit("2/minute")
def handle_forgot_password():
    """
    User forgot password
    Input:
        email: User's email address
    Output:
        200: A reset password email is sent to the user if the email exists
        400: If the request body is invalid or email is missing
    """
    data = request.get_json()
    if not data or not data.get("email"):
        return jsonify(error="Email is required"), 400

    # Sanitize and canonicalize email
    email = sanitize_email(data.get("email"))
    canonical_email = canonicalize_email(email)

    # Fetch user by email or canonical email
    user = User.get_by(email=email) or User.get_by(email=canonical_email)

    # Send reset password email if user exists
    if user:
        LOG.d("Sending reset password email to %s", user.email)
        send_reset_password_email(user)

    # Always return 200 to avoid revealing whether the email exists
    return jsonify(ok=True), 200
