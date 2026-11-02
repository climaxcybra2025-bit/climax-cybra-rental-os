"""Staff screens: dashboard plus list / detail / create / edit / archive for every record type."""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_POST

from apps.landlords.models import Lease
from apps.operations.models import CleaningDuty, Ticket
from apps.properties.models import Space
from apps.records.models import AuditEntry, RecordFile
from apps.tenancies.models import Payment, Tenancy

from .forms import FileUploadForm
from .resources import BY_SLUG, NAV_GROUPS


def home(request):
    """Send each user to the right place after login."""
    if not request.user.is_authenticated:
        return redirect("login")
    membership = getattr(request, "membership", None)
    if membership is None:
        if request.user.is_superuser:
            return redirect("admin:index")
        return render(request, "web/no_workspace.html", status=403)
    if membership.role == "tenant":
        return redirect("portal:home")
    return redirect("web:dashboard")


class StaffRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        membership = getattr(request, "membership", None)
        if membership is None or not membership.is_staff_role:
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def base_context(self, **extra):
        return {"nav_groups": [(title, [BY_SLUG[s] for s in slugs]) for title, slugs in NAV_GROUPS], **extra}


class DashboardView(StaffRequiredMixin, View):
    def get(self, request):
        ws = request.workspace
        today = date.today()
        spaces = Space.objects.active().filter(workspace=ws)
        counts = {s: spaces.filter(status=s).count() for s, _label in Space.Status.choices}
        leases_soon = (
            Lease.objects.active()
            .filter(workspace=ws, end_date__isnull=False, end_date__gte=today, end_date__lte=today + timedelta(days=90))
            .select_related("property", "landlord")
            .order_by("end_date")
        )
        open_tickets = (
            Ticket.objects.active()
            .filter(workspace=ws)
            .exclude(status__in=["completed", "closed"])
            .select_related("property", "space")
            .order_by("priority", "-created_at")
        )
        pending_payments = (
            Payment.objects.active().filter(workspace=ws, status="pending").select_related("tenancy", "lease")
        )
        arrivals = (
            Tenancy.objects.active()
            .filter(workspace=ws, status="reserved", start_date__range=(today, today + timedelta(days=7)))
            .select_related("person", "space")
            .order_by("start_date")
        )
        departures = (
            Tenancy.objects.active()
            .filter(
                workspace=ws, status__in=["active", "notice_given"], end_date__range=(today, today + timedelta(days=7))
            )
            .select_related("person", "space")
            .order_by("end_date")
        )
        cleaning_today = (
            CleaningDuty.objects.active().filter(workspace=ws, due_date=today).select_related("person", "property")
        )
        total = spaces.count()
        occupied = counts.get("rented", 0)
        ctx = self.base_context(
            total_spaces=total,
            occupied=occupied,
            occupancy=round(100 * occupied / total) if total else 0,
            available=counts.get("available", 0) + counts.get("advertised", 0),
            space_counts=[(label, counts.get(s, 0), s) for s, label in Space.Status.choices],
            leases_soon=[(lease, lease.days_to_expiry(today)) for lease in leases_soon],
            open_tickets=open_tickets[:10],
            urgent_count=open_tickets.filter(priority="urgent").count(),
            pending_payments=pending_payments[:10],
            pending_count=pending_payments.count(),
            arrivals=arrivals,
            departures=departures,
            cleaning_today=cleaning_today,
            today=today,
        )
        return render(request, "web/dashboard.html", ctx)


class ResourceMixin(StaffRequiredMixin):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.resource = BY_SLUG.get(kwargs["resource"])
        if self.resource is None:
            raise Http404

    def queryset(self):
        return self.resource.model.objects.active().filter(workspace=self.request.workspace)

    def get_object(self, pk):
        return get_object_or_404(self.queryset(), pk=pk)

    def ctx(self, **extra):
        return self.base_context(resource=self.resource, **extra)


class ResourceListView(ResourceMixin, View):
    def get(self, request, resource):
        res = self.resource
        qs = self.queryset()
        q = request.GET.get("q", "").strip()
        if q and res.search:
            cond = Q()
            for f in res.search:
                cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond)
        active_filters = {}
        filter_options = []
        for name in res.filters:
            model_field = res.model._meta.get_field(name)
            value = request.GET.get(name, "")
            if value:
                qs = qs.filter(**{name: value})
                active_filters[name] = value
            filter_options.append((name, model_field.verbose_name, [c for c in model_field.choices if c[0]], value))
        # Related-list filter, e.g. ?property=3 from a property page.
        for key, value in request.GET.items():
            if key.endswith("_id") or key in {"q", "page"} or key in res.filters:
                continue
            try:
                f = res.model._meta.get_field(key)
            except Exception:
                continue
            if f.is_relation and value.isdigit():
                qs = qs.filter(**{key: value})
                active_filters[key] = value
        rows = list(qs[:500])
        return render(
            request,
            "web/list.html",
            self.ctx(
                rows=rows, q=q, filter_options=filter_options, active_filters=active_filters, truncated=len(rows) == 500
            ),
        )


class ResourceFormView(ResourceMixin, View):
    def get_form(self, obj=None, data=None, files=None):
        initial = {}
        if obj is None:
            for key, value in self.request.GET.items():
                if key in self.resource.form_fields:
                    initial[key] = value
            if "currency" in self.resource.form_fields:
                initial.setdefault("currency", self.request.workspace.base_currency)
            if "country" in self.resource.form_fields:
                initial.setdefault("country", self.request.workspace.country)
            if "time_zone" in self.resource.form_fields:
                initial.setdefault("time_zone", self.request.workspace.time_zone)
            if "occurred_at" in self.resource.form_fields:
                initial.setdefault("occurred_at", timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
            if "paid_on" in self.resource.form_fields:
                initial.setdefault("paid_on", date.today().isoformat())
        return self.resource.form_class(
            data=data,
            files=files,
            instance=obj,
            initial=initial,
            workspace=self.request.workspace,
            user=self.request.user,
        )

    def get(self, request, resource, pk=None):
        obj = self.get_object(pk) if pk else None
        return render(request, "web/form.html", self.ctx(form=self.get_form(obj), obj=obj))

    def post(self, request, resource, pk=None):
        obj = self.get_object(pk) if pk else None
        form = self.get_form(obj, data=request.POST, files=request.FILES)
        if form.is_valid():
            saved = form.save()
            messages.success(request, _("Saved."))
            nxt = request.GET.get("next")
            if nxt and nxt.startswith("/"):
                return HttpResponseRedirect(nxt)
            return redirect("web:detail", resource=self.resource.slug, pk=saved.pk)
        return render(request, "web/form.html", self.ctx(form=form, obj=obj), status=400)


class ResourceDetailView(ResourceMixin, View):
    def get(self, request, resource, pk):
        obj = self.get_object(pk)
        res = self.resource
        fields = []
        for name in res.detail_fields:
            f = res.model._meta.get_field(name)
            fields.append((f.verbose_name, name))
        sections = []
        for rel in res.related:
            rel_res = BY_SLUG[rel.resource]
            rows = list(rel_res.model.objects.active().filter(workspace=request.workspace, **{rel.fk: obj})[:100])
            sections.append({"title": rel.title, "res": rel_res, "rows": rows, "fk": rel.fk})
        ct = ContentType.objects.get_for_model(obj)
        files = RecordFile.objects.active().filter(workspace=request.workspace, content_type=ct, object_id=obj.pk)
        history = AuditEntry.objects.filter(workspace_id=request.workspace.pk, content_type=ct, object_id=obj.pk)[:50]
        return render(
            request,
            "web/detail.html",
            self.ctx(
                obj=obj,
                fields=fields,
                sections=sections,
                files=files,
                history=history,
                upload_form=FileUploadForm(initial={"attachment_kind": res.attachment_kind}),
            ),
        )


class ResourceArchiveView(ResourceMixin, View):
    def post(self, request, resource, pk):
        obj = self.get_object(pk)
        obj.delete()  # archives
        messages.success(request, _("Archived. The record and its history are kept."))
        return redirect("web:list", resource=self.resource.slug)


class FileUploadView(ResourceMixin, View):
    def post(self, request, resource, pk):
        obj = self.get_object(pk)
        form = FileUploadForm(request.POST, request.FILES)
        if form.is_valid():
            ct = ContentType.objects.get_for_model(obj)
            for f in form.cleaned_data["attachments"]:
                RecordFile.objects.create(
                    workspace=request.workspace,
                    content_type=ct,
                    object_id=obj.pk,
                    kind=form.cleaned_data["attachment_kind"],
                    file=f,
                    title=form.cleaned_data["title"] or f.name,
                    uploaded_by=request.user,
                )
            messages.success(request, _("Files attached."))
        else:
            messages.error(request, _("Choose at least one file."))
        return redirect("web:detail", resource=self.resource.slug, pk=obj.pk)


class FileDownloadView(StaffRequiredMixin, View):
    """Files are served only to staff of the owning workspace, never from a public URL."""

    def get(self, request, pk):
        from django.http import FileResponse

        rf = get_object_or_404(RecordFile.objects.active(), pk=pk, workspace=request.workspace)
        return FileResponse(rf.file.open("rb"), as_attachment=False, filename=rf.file.name.rsplit("/", 1)[-1])


class PaymentConfirmView(StaffRequiredMixin, View):
    def post(self, request, pk):
        payment = get_object_or_404(Payment.objects.active(), pk=pk, workspace=request.workspace)
        if payment.status != Payment.Status.CONFIRMED:
            payment.status = Payment.Status.CONFIRMED
            payment.confirmed_by = request.user
            payment.confirmed_at = timezone.now()
            payment.save()
            messages.success(request, _("Payment confirmed."))
        return redirect(request.POST.get("next") or reverse("web:detail", args=["payments", pk]))


@require_POST
def switch_workspace(request):
    from apps.workspaces.middleware import SESSION_KEY

    ws_id = request.POST.get("workspace")
    if request.user.is_authenticated and request.user.memberships.filter(workspace_id=ws_id).exists():
        request.session[SESSION_KEY] = int(ws_id)
    return redirect("home")
