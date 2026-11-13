from .approvals import approval_list, request_approve, request_reject, request_revoke  # noqa: F401
from .monitor import ban_lift, ban_list, monitor, people_search  # noqa: F401
from .camp import camp, camp_action  # noqa: F401
from .canteen import canteen  # noqa: F401
from .dispatch import dispatch, dispatch_assign, trip_delete, trip_edit, trip_remove  # noqa: F401
from .kpp import kpp, kpp_action, kpp_scan, kpp_trip_action  # noqa: F401
from .passes import group_arrive, group_pass, pass_view, print_passes  # noqa: F401
from .reports import reports  # noqa: F401
from .rides import ride_action, ride_create, ride_detail, ride_list  # noqa: F401
from .requests import (  # noqa: F401
    cargo_file, dashboard, import_parse, import_template, request_cancel, request_create, request_detail, request_edit,
    request_list,
)
