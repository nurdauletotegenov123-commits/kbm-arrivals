from django.contrib.auth import get_user_model
from rest_framework import filters, viewsets
from rest_framework.permissions import IsAuthenticated

from .serializers import UserInfoSerializer

User = get_user_model()


class UserSearchViewSet(viewsets.ReadOnlyModelViewSet):
    """Поиск сотрудников для маршрута наряда: /api/v1/users/?search=Иванов"""

    queryset = User.objects.filter(is_active=True).select_related("department")
    serializer_class = UserInfoSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # UserSearchSelect ожидает массив
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "full_name", "first_name", "last_name", "surname", "iin", "position"]
