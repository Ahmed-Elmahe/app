import arrow
from flask import g, jsonify
from app.api.base import api_bp, require_api_auth
from app.models import PhoneReservation, PhoneMessage


def get_reservation_for_user(reservation_id, user):
    """Helper function to get a valid reservation for the user"""
    reservation = PhoneReservation.get(reservation_id)
    if not reservation or reservation.user_id != user.id:
        return None
    return reservation


def get_messages_for_reservation(reservation):
    """Helper function to fetch messages within the reservation timeframe"""
    return PhoneMessage.filter(
        PhoneMessage.number_id == reservation.number.id,
        PhoneMessage.created_at > reservation.start,
        PhoneMessage.created_at < reservation.end,
    ).all()


@api_bp.route("/phone/reservations/<int:reservation_id>", methods=["GET", "POST"])
@require_api_auth
def phone_messages(reservation_id):
    """
    Return messages exchanged during a specific reservation.

    Args:
        reservation_id (int): The reservation identifier.

    Output:
        - messages: List of messages containing:
            - id: Message ID
            - from_number: Phone number the message is from
            - body: Message content
            - created_at: Time of creation (e.g., '5 minutes ago')
        - ended: Boolean indicating if the reservation has ended
    """
    user = g.user
    reservation = get_reservation_for_user(reservation_id, user)

    if not reservation:
        return jsonify(error="Invalid reservation or unauthorized access"), 403

    messages = get_messages_for_reservation(reservation)

    # Check if no messages were found
    if not messages:
        return jsonify(error="No messages found for this reservation"), 404

    # Prepare messages with human-readable timestamp
    message_data = [
        {
            "id": message.id,
            "from_number": message.from_number,
            "body": message.body,
            "created_at": arrow.get(message.created_at).humanize(),
        }
        for message in messages
    ]

    # Check if the reservation has ended
    reservation_ended = reservation.end < arrow.now()

    return jsonify(messages=message_data, ended=reservation_ended), 200
