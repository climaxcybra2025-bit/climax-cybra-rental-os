from django.urls import path

from . import integration_views as iv
from . import views

app_name = "web"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("files/<int:pk>/", views.FileDownloadView.as_view(), name="file"),
    path("payments/<int:pk>/confirm/", views.PaymentConfirmView.as_view(), name="payment-confirm"),
    path("workspace/", views.switch_workspace, name="switch-workspace"),
    path("api-keys/", iv.ApiKeysView.as_view(), name="api-keys"),
    path("quickbooks/callback/", iv.QuickBooksCallbackView.as_view(), name="qbo-callback"),
    path("quickbooks/<int:pk>/connect/", iv.QuickBooksConnectView.as_view(), name="qbo-connect"),
    path("quickbooks/<int:pk>/mapping/", iv.QuickBooksMappingView.as_view(), name="qbo-mapping"),
    path("quickbooks/<int:pk>/push/", iv.QuickBooksPushView.as_view(), name="qbo-push"),
    path("quickbooks/<int:pk>/disconnect/", iv.QuickBooksDisconnectView.as_view(), name="qbo-disconnect"),
    path("connections/<int:pk>/test/", iv.ConnectionTestView.as_view(), name="connection-test"),
    path("connections/<int:pk>/sync/", iv.ConnectionSyncView.as_view(), name="connection-sync"),
    path("listing-channels/<int:pk>/post/", iv.ListingChannelPostView.as_view(), name="listing-channel-post"),
    path("listing-channels/<int:pk>/posted/", iv.ListingChannelMarkPostedView.as_view(), name="listing-channel-posted"),
    path("correspondence/<int:pk>/reply/", iv.CorrespondenceReplyView.as_view(), name="correspondence-reply"),
    path("<slug:resource>/", views.ResourceListView.as_view(), name="list"),
    path("<slug:resource>/new/", views.ResourceFormView.as_view(), name="create"),
    path("<slug:resource>/<int:pk>/", views.ResourceDetailView.as_view(), name="detail"),
    path("<slug:resource>/<int:pk>/edit/", views.ResourceFormView.as_view(), name="edit"),
    path("<slug:resource>/<int:pk>/archive/", views.ResourceArchiveView.as_view(), name="archive"),
    path("<slug:resource>/<int:pk>/files/", views.FileUploadView.as_view(), name="upload"),
]
