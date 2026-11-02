"""Ticket notifications: urgent tickets email the workspace's urgent recipients."""

import logging

from django.core.mail import send_mail
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Ticket

logger = logging.getLogger(__name__)


def notify_urgent_ticket(ticket):
    recipients = ticket.workspace.urgent_recipients()
    if not recipients:
        logger.warning("Urgent ticket %s but workspace %s has no urgent recipients", ticket.pk, ticket.workspace_id)
        return 0
    location = f"{ticket.property}" + (f" / {ticket.space.name}" if ticket.space_id else "")
    reporter = str(ticket.reported_by) if ticket.reported_by_id else "staff"
    body = (
        f"Urgent ticket #{ticket.pk}: {ticket.title}\n"
        f"Where: {location}\n"
        f"Category: {ticket.get_category_display()}\n"
        f"Reported by: {reporter}\n\n"
        f"{ticket.description}\n"
    )
    return send_mail(
        subject=f"URGENT: {ticket.title} ({ticket.property})",
        message=body,
        from_email=None,
        recipient_list=recipients,
        fail_silently=False,
    )


@receiver(post_save, sender=Ticket)
def on_ticket_saved(sender, instance, created, **kwargs):
    if created and instance.priority == Ticket.Priority.URGENT and not kwargs.get("raw"):
        transaction.on_commit(lambda: notify_urgent_ticket(instance))
