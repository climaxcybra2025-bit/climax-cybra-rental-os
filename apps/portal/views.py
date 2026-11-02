"""Tenant portal: tenants see their home, payments and cleaning turns, and open tickets with photos."""

from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View

from apps.operations.models import CleaningDuty, Ticket
from apps.people.models import Person
from apps.records.models import RecordFile
from apps.tenancies.models import Payment, Tenancy
from apps.web.forms import MultipleFileField


class TicketForm(forms.ModelForm):
    photos = MultipleFileField(label=gettext_lazy("Photos or video"))

    class Meta:
        model = Ticket
        fields = ["category", "priority", "title", "description"]
        labels = {
            "title": gettext_lazy("What is the problem?"),
            "description": gettext_lazy("Details"),
            "priority": gettext_lazy("How urgent is it?"),
        }
        widgets = {"priority": forms.RadioSelect}


TENANT_PURPOSES = {"rent", "deposit", "guest_fee", "cleaning", "other"}


class PaymentProofForm(forms.Form):
    amount = forms.DecimalField(label=gettext_lazy("Amount"), max_digits=12, decimal_places=2, min_value=0.01)
    paid_on = forms.DateField(label=gettext_lazy("Paid on"), widget=forms.DateInput(attrs={"type": "date"}))
    purpose = forms.ChoiceField(
        label=gettext_lazy("For"),
        choices=[
            (k, v) for k, v in Payment.Purpose.choices if k in {"rent", "deposit", "guest_fee", "cleaning", "other"}
        ],
    )
    method = forms.CharField(label=gettext_lazy("Method"), initial="e-transfer", max_length=40)
    slip = MultipleFileField(label=gettext_lazy("Screenshot or receipt"), required=True)


class TenantMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        membership = getattr(request, "membership", None)
        if membership is None or membership.role != "tenant":
            return redirect("home")
        self.person = Person.objects.active().filter(workspace=request.workspace, user=request.user).first()
        if self.person is None:
            return render(request, "portal/not_linked.html", status=403)
        self.tenancy = (
            Tenancy.objects.active()
            .filter(person=self.person, status__in=["active", "notice_given", "reserved"])
            .select_related("space__property")
            .order_by("start_date")
            .first()
        )
        return super().dispatch(request, *args, **kwargs)

    def attach(self, obj, files, kind):
        ct = ContentType.objects.get_for_model(obj)
        for f in files:
            RecordFile.objects.create(
                workspace=self.request.workspace,
                content_type=ct,
                object_id=obj.pk,
                kind=kind,
                file=f,
                title=f.name,
                uploaded_by=self.request.user,
            )

    def render_home(self, request, ticket_form=None, payment_form=None, status=200):
        prop = self.tenancy.space.property if self.tenancy else None
        ctx = {
            "person": self.person,
            "tenancy": self.tenancy,
            "property": prop,
            "house_info": (prop.house_info or {}) if prop else {},
            "emergency_phone": request.workspace.emergency_phone,
            "tickets": Ticket.objects.active().filter(reported_by=self.person).order_by("-created_at")[:20],
            "payments": Payment.objects.active().filter(tenancy__person=self.person).order_by("-paid_on")[:20],
            "cleaning": CleaningDuty.objects.active()
            .filter(person=self.person, due_date__gte=date.today())
            .order_by("due_date")[:5],
            "ticket_form": ticket_form or TicketForm(initial={"priority": "normal"}),
            "payment_form": payment_form or PaymentProofForm(initial={"paid_on": date.today().isoformat()}),
        }
        return render(request, "portal/home.html", ctx, status=status)


class PortalHomeView(TenantMixin, View):
    def get(self, request):
        return self.render_home(request)


class PortalTicketCreateView(TenantMixin, View):
    def post(self, request):
        if self.tenancy is None:
            messages.error(request, _("You have no active tenancy, so tickets cannot be opened."))
            return redirect("portal:home")
        form = TicketForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_home(request, ticket_form=form, status=400)
        ticket = form.save(commit=False)
        ticket.workspace = request.workspace
        ticket.property = self.tenancy.space.property
        ticket.space = self.tenancy.space
        ticket.reported_by = self.person
        ticket.save()
        self.attach(ticket, form.cleaned_data["photos"], RecordFile.Kind.PHOTO)
        if ticket.priority == Ticket.Priority.URGENT:
            messages.success(request, _("Urgent ticket sent. Our team has been emailed."))
        else:
            messages.success(request, _("Ticket sent. We will get back to you."))
        return redirect("portal:home")


class PortalPaymentProofView(TenantMixin, View):
    """Tenant uploads an e-transfer screenshot; staff confirm it before it counts."""

    def post(self, request):
        if self.tenancy is None:
            messages.error(request, _("You have no active tenancy."))
            return redirect("portal:home")
        form = PaymentProofForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_home(request, payment_form=form, status=400)
        payment = Payment.objects.create(
            workspace=request.workspace,
            direction=Payment.Direction.IN,
            purpose=form.cleaned_data["purpose"],
            tenancy=self.tenancy,
            amount=form.cleaned_data["amount"],
            currency=self.tenancy.currency,
            paid_on=form.cleaned_data["paid_on"],
            method=form.cleaned_data["method"],
            status=Payment.Status.PENDING,
            notes=_("Submitted by tenant from the portal"),
        )
        self.attach(payment, form.cleaned_data["slip"], RecordFile.Kind.PAYMENT_SLIP)
        messages.success(request, _("Thanks. Your payment proof was received and will be confirmed by the team."))
        return redirect("portal:home")


class PortalTicketDetailView(TenantMixin, View):
    def get(self, request, pk):
        ticket = get_object_or_404(Ticket.objects.active(), pk=pk, reported_by=self.person)
        return render(
            request, "portal/ticket.html", {"ticket": ticket, "emergency_phone": request.workspace.emergency_phone}
        )
