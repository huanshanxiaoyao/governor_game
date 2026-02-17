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
    path("games/<int:game_id>/medical-level/", views.MedicalLevelView.as_view(), name="game-medical-level"),
    path("games/<int:game_id>/summary/", views.GameSummaryView.as_view(), name="game-summary"),
    path("games/<int:game_id>/agents/", views.AgentListView.as_view(), name="game-agents"),
    path("games/<int:game_id>/agents/<int:agent_id>/chat/", views.AgentChatView.as_view(), name="agent-chat"),
    # Event logs
    path("games/<int:game_id>/events/", views.EventLogListView.as_view(), name="game-events"),
    # Promises
    path("games/<int:game_id>/promises/", views.PromiseListView.as_view(), name="game-promises"),
    # Negotiation endpoints
    path("games/<int:game_id>/negotiations/active/", views.ActiveNegotiationView.as_view(), name="negotiation-active"),
    path("games/<int:game_id>/negotiations/start-irrigation/", views.StartIrrigationNegotiationView.as_view(), name="negotiation-start-irrigation"),
    path("games/<int:game_id>/negotiations/<int:session_id>/chat/", views.NegotiationChatView.as_view(), name="negotiation-chat"),
]
