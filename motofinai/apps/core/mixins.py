"""Reusable view mixins for common functionality."""

from django.db.models import Q


class SearchSortFilterMixin:
    """
    Mixin providing async search, sort, and filter functionality for ListViews.

    Usage:
        class MyListView(SearchSortFilterMixin, LoginRequiredMixin, ListView):
            model = MyModel
            search_fields = ['name', 'description']
            filter_fields = ['status', 'type']
            sort_fields = {
                'name': 'name',
                'price': 'purchase_price',
                'created': 'created_at',
            }
            default_sort = '-created_at'
            partial_template_name = 'pages/myapp/_mymodel_table.html'
    """

    search_fields = []
    filter_fields = []
    sort_fields = {}
    default_sort = '-created_at'
    partial_template_name = None

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.apply_search(queryset)
        queryset = self.apply_filters(queryset)
        queryset = self.apply_sorting(queryset)
        return queryset

    def apply_search(self, queryset):
        """Apply search filter based on search_fields."""
        search_query = self.request.GET.get('q', '').strip()
        if search_query and self.search_fields:
            q_objects = Q()
            for field in self.search_fields:
                q_objects |= Q(**{f'{field}__icontains': search_query})
            queryset = queryset.filter(q_objects)
        return queryset

    def apply_filters(self, queryset):
        """Apply dropdown filters based on filter_fields."""
        for field in self.filter_fields:
            value = self.request.GET.get(field, '').strip()
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def apply_sorting(self, queryset):
        """Apply column sorting."""
        sort_by = self.request.GET.get('sort', '')
        order = self.request.GET.get('order', 'asc')

        if sort_by in self.sort_fields:
            field = self.sort_fields[sort_by]
            if order == 'desc':
                field = f'-{field}'
            queryset = queryset.order_by(field)
        elif self.default_sort:
            queryset = queryset.order_by(self.default_sort)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'search_query': self.request.GET.get('q', ''),
            'current_sort': self.request.GET.get('sort', ''),
            'current_order': self.request.GET.get('order', 'asc'),
            'current_filters': {
                field: self.request.GET.get(field, '')
                for field in self.filter_fields
            },
            'filter_choices': self.get_filter_choices(),
            'sort_fields': list(self.sort_fields.keys()),
        })
        return context

    def get_filter_choices(self):
        """Override to provide filter dropdown choices."""
        return {}

    def get_template_names(self):
        """Return partial template for HTMX requests."""
        if getattr(self.request, 'htmx', False) and self.partial_template_name:
            return [self.partial_template_name]
        return super().get_template_names()

    def build_query_string(self, **overrides):
        """Build query string preserving current filters/search/sort."""
        params = {
            'q': self.request.GET.get('q', ''),
            'sort': self.request.GET.get('sort', ''),
            'order': self.request.GET.get('order', ''),
        }
        for field in self.filter_fields:
            params[field] = self.request.GET.get(field, '')

        params.update(overrides)

        # Remove empty values
        params = {k: v for k, v in params.items() if v}

        if params:
            return '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        return ''
