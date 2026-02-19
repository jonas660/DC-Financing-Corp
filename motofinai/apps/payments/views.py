from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict
from datetime import datetime, timedelta
from calendar import monthrange
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views import View
from django.views.generic import FormView, TemplateView

from motofinai.apps.core.mixins import SearchSortFilterMixin
from motofinai.apps.loans.models import LoanApplication, PaymentSchedule

from .forms import PaymentRecordForm
from .models import Payment


class PaymentScheduleListView(LoginRequiredMixin, TemplateView):
    template_name = "pages/payments/schedule_list.html"
    partial_template_name = "pages/payments/_schedule_table.html"
    required_roles = ("admin", "finance")

    def get_queryset(self):
        reference_date = timezone.now().date()
        PaymentSchedule.objects.mark_overdue(reference_date)

        schedules = PaymentSchedule.objects.select_related(
            "loan_application",
            "loan_application__motor",
        ).order_by("due_date", "sequence")

        # Search by customer name (applicant name) - support both 'customer' and 'q' params
        customer_search = self.request.GET.get("customer") or self.request.GET.get("q")
        if customer_search:
            schedules = schedules.filter(
                Q(loan_application__applicant_first_name__icontains=customer_search) |
                Q(loan_application__applicant_last_name__icontains=customer_search) |
                Q(loan_application__applicant_email__icontains=customer_search)
            )

        # Filter by status
        status = self.request.GET.get("status")
        if status in dict(PaymentSchedule.Status.choices):
            schedules = schedules.filter(status=status)

        # Sorting
        sort_by = self.request.GET.get('sort', '')
        order = self.request.GET.get('order', 'asc')
        sort_fields = {
            'customer': 'loan_application__applicant_last_name',
            'due_date': 'due_date',
            'amount': 'total_amount',
            'status': 'status',
        }
        if sort_by in sort_fields:
            field = sort_fields[sort_by]
            if order == 'desc':
                field = f'-{field}'
            schedules = schedules.order_by(field)

        return schedules

    def get_template_names(self):
        """Return partial template for HTMX requests."""
        if getattr(self.request, 'htmx', False) and self.partial_template_name:
            return [self.partial_template_name]
        return [self.template_name]

    def get_summary(self, schedules):
        aggregates = schedules.aggregate(
            due_total=Sum("total_amount", filter=Q(status=PaymentSchedule.Status.DUE)),
            overdue_total=Sum("total_amount", filter=Q(status=PaymentSchedule.Status.OVERDUE)),
            paid_total=Sum("total_amount", filter=Q(status=PaymentSchedule.Status.PAID)),
            due_count=Count("id", filter=Q(status=PaymentSchedule.Status.DUE)),
            overdue_count=Count("id", filter=Q(status=PaymentSchedule.Status.OVERDUE)),
            paid_count=Count("id", filter=Q(status=PaymentSchedule.Status.PAID)),
        )
        due_total = aggregates["due_total"] or Decimal("0.00")
        overdue_total = aggregates["overdue_total"] or Decimal("0.00")
        paid_total = aggregates["paid_total"] or Decimal("0.00")
        denominator = due_total + overdue_total + paid_total
        collection_rate = Decimal("0.00")
        if denominator > 0:
            collection_rate = (paid_total / denominator * Decimal("100")).quantize(Decimal("0.01"))
        return {
            "due_total": due_total,
            "overdue_total": overdue_total,
            "paid_total": paid_total,
            "due_count": aggregates["due_count"] or 0,
            "overdue_count": aggregates["overdue_count"] or 0,
            "paid_count": aggregates["paid_count"] or 0,
            "collection_rate": collection_rate,
            "pending_amount": due_total + overdue_total,
            "total_collected": paid_total,
        }

    def get_chart_data(self):
        """Generate data for payment tracking charts"""
        now = timezone.now()

        # Get last 6 months of data for charts
        months_data = []
        collection_rates = []

        for i in range(5, -1, -1):
            month_date = now - timedelta(days=30 * i)
            month_start = month_date.replace(day=1).date()
            _, last_day = monthrange(month_date.year, month_date.month)
            month_end = month_date.replace(day=last_day).date()

            month_schedules = PaymentSchedule.objects.filter(
                due_date__gte=month_start,
                due_date__lte=month_end
            )

            month_summary = month_schedules.aggregate(
                collected=Sum("total_amount", filter=Q(status=PaymentSchedule.Status.PAID)),
                pending=Sum("total_amount", filter=~Q(status=PaymentSchedule.Status.PAID)),
            )

            collected = float(month_summary["collected"] or 0)
            pending = float(month_summary["pending"] or 0)
            total = collected + pending
            rate = (collected / total * 100) if total > 0 else 0

            months_data.append({
                'month': month_date.strftime('%b'),
                'collected': collected,
                'pending': pending,
            })
            collection_rates.append({
                'month': month_date.strftime('%b'),
                'rate': round(rate, 2),
            })

        return {
            'months_data': months_data,
            'collection_rates': collection_rates,
        }

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        schedules = self.get_queryset()

        # Add pagination
        paginator = Paginator(schedules, 25)  # 25 items per page
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        # Build sort URLs for template
        current_sort = self.request.GET.get('sort', '')
        current_order = self.request.GET.get('order', 'asc')

        def get_sort_url(field):
            new_order = 'desc' if current_sort == field and current_order == 'asc' else 'asc'
            params = dict(self.request.GET)
            params['sort'] = [field]
            params['order'] = [new_order]
            return '&'.join(f'{k}={v[0]}' for k, v in params.items() if v and v[0])

        # Note: Summary uses all schedules, not just paginated ones
        context.update(
            {
                "schedules": page_obj,
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
                "paginator": paginator,
                "summary": self.get_summary(schedules),
                "loans": LoanApplication.objects.order_by("-submitted_at")[:20],
                "chart_data": self.get_chart_data(),
                "page_title": "Payment Tracking",
                # Search/sort/filter context
                "search_query": self.request.GET.get("customer") or self.request.GET.get("q", ""),
                "current_sort": current_sort,
                "current_order": current_order,
                "current_filters": {
                    "status": self.request.GET.get("status", ""),
                },
                "filter_choices": {
                    "status": [("", "All Status")] + list(PaymentSchedule.Status.choices),
                },
                "sort_url_customer": get_sort_url('customer'),
                "sort_url_due_date": get_sort_url('due_date'),
                "sort_url_amount": get_sort_url('amount'),
                "sort_url_status": get_sort_url('status'),
            }
        )
        return context


class RecordPaymentView(LoginRequiredMixin, FormView):
    template_name = "pages/payments/record_payment.html"
    form_class = PaymentRecordForm
    required_roles = ("admin", "finance")

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.schedule = get_object_or_404(
            PaymentSchedule.objects.select_related("loan_application", "loan_application__motor"),
            pk=kwargs["pk"],
        )
        self.schedule.refresh_status()
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> Dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["schedule"] = self.schedule
        return kwargs

    def form_valid(self, form: PaymentRecordForm) -> HttpResponse:
        try:
            Payment.objects.create(
                schedule=self.schedule,
                loan_application=self.schedule.loan_application,
                amount=form.cleaned_data["amount"],
                payment_date=form.cleaned_data["payment_date"],
                reference=form.cleaned_data.get("reference", ""),
                notes=form.cleaned_data.get("notes", ""),
                recorded_by=self.request.user,
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, error_list in exc.message_dict.items():
                    for message in error_list:
                        if field in form.fields:
                            form.add_error(field, message)
                        else:
                            form.add_error(None, message)
            else:
                form.add_error(None, "; ".join(exc.messages))
            return self.form_invalid(form)
        messages.success(self.request, "Payment recorded successfully.")
        return redirect(self.get_success_url())

    def form_invalid(self, form: PaymentRecordForm) -> HttpResponse:
        context = self.get_context_data(form=form)
        return self.render_to_response(context, status=400)

    def get_success_url(self) -> str:
        return reverse("payments:schedule-list")

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "schedule": self.schedule,
                "loan": self.schedule.loan_application,
            }
        )
        return context


class PaymentScheduleSearchView(LoginRequiredMixin, View):
    """API endpoint for async payment schedule search."""
    required_roles = ("admin", "finance")

    def get(self, request: HttpRequest) -> JsonResponse:
        try:
            customer_search = request.GET.get("customer", "").strip()
            status = request.GET.get("status", "").strip()

            # Mark overdue schedules
            reference_date = timezone.now().date()
            PaymentSchedule.objects.mark_overdue(reference_date)

            # Get filtered schedules
            schedules = PaymentSchedule.objects.select_related(
                "loan_application",
                "loan_application__motor",
            ).order_by("due_date", "sequence")

            # Filter by customer name
            if customer_search:
                schedules = schedules.filter(
                    Q(loan_application__applicant_first_name__icontains=customer_search) |
                    Q(loan_application__applicant_last_name__icontains=customer_search) |
                    Q(loan_application__applicant_email__icontains=customer_search)
                )

            # Filter by status
            if status and status in dict(PaymentSchedule.Status.choices):
                schedules = schedules.filter(status=status)

            # Build response data
            data = []
            for schedule in schedules[:50]:  # Limit to 50 results
                try:
                    data.append({
                        "id": schedule.id,
                        "schedule_id": f"SCH-{schedule.id:06d}",
                        "customer": f"{schedule.loan_application.applicant_first_name} {schedule.loan_application.applicant_last_name}",
                        "email": schedule.loan_application.applicant_email,
                        "motor": schedule.loan_application.motor.display_name,
                        "due_date": schedule.due_date.strftime("%Y-%m-%d"),
                        "amount": str(schedule.total_amount),
                        "status": schedule.status,
                        "status_display": schedule.get_status_display(),
                    })
                except (AttributeError, TypeError):
                    # Skip schedules with missing relationships
                    continue

            return JsonResponse({"results": data, "count": len(data)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({"error": str(e), "results": [], "count": 0}, status=400)
