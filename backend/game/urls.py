from django.urls import path
from . import views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="api-login"),
    path("logout/", views.LogoutView.as_view(), name="api-logout"),
    path("games/", views.GameListCreateView.as_view(), name="game-list-create"),
    path("games/<int:game_id>/", views.GameDetailView.as_view(), name="game-detail"),
    path("games/<int:game_id>/invest/", views.InvestView.as_view(), name="game-invest"),
    path("games/<int:game_id>/advance/", views.AdvanceSeasonView.as_view(), name="game-advance"),
    path("games/<int:game_id>/tax-rate/", views.TaxRateView.as_view(), name="game-tax-rate"),
    path("games/<int:game_id>/summary/", views.GameSummaryView.as_view(), name="game-summary"),
]
