from django.urls import path
from . import views, views_prefecture

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="api-login"),
    path("logout/", views.LogoutView.as_view(), name="api-logout"),
    path("games/", views.GameListCreateView.as_view(), name="game-list-create"),
    path("games/<int:game_id>/", views.GameDetailView.as_view(), name="game-detail"),
    path("games/<int:game_id>/annual-review/", views.AnnualReviewSubmitView.as_view(), name="game-annual-review"),
    path("games/<int:game_id>/invest/", views.InvestView.as_view(), name="game-invest"),
    path("games/<int:game_id>/land-survey/", views.RequestLandSurveyView.as_view(), name="game-land-survey"),
    path("games/<int:game_id>/check-bribes/", views.CheckBribesView.as_view(), name="game-check-bribes"),
    path("games/<int:game_id>/respond-bribe/", views.RespondBribeView.as_view(), name="game-respond-bribe"),
    path("games/<int:game_id>/advance/", views.AdvanceSeasonView.as_view(), name="game-advance"),
    path("games/<int:game_id>/tax-rate/", views.TaxRateView.as_view(), name="game-tax-rate"),
    path("games/<int:game_id>/commercial-tax-rate/", views.CommercialTaxRateView.as_view(), name="game-commercial-tax-rate"),
    path("games/<int:game_id>/summary/", views.GameSummaryView.as_view(), name="game-summary"),
    path("games/<int:game_id>/summary-v2/", views.GameSummaryV2View.as_view(), name="game-summary-v2"),
    path("games/<int:game_id>/staff/", views.StaffInfoView.as_view(), name="game-staff"),
    path("games/<int:game_id>/agents/", views.AgentListView.as_view(), name="game-agents"),
    path("games/<int:game_id>/agents/<int:agent_id>/chat/", views.AgentChatView.as_view(), name="agent-chat"),
    # Event logs
    path("games/<int:game_id>/events/", views.EventLogListView.as_view(), name="game-events"),
    # Promises
    path("games/<int:game_id>/promises/", views.PromiseListView.as_view(), name="game-promises"),
    # Negotiation endpoints
    path("games/<int:game_id>/negotiations/active/", views.ActiveNegotiationView.as_view(), name="negotiation-active"),
    path("games/<int:game_id>/negotiations/active-list/", views.ActiveNegotiationsListView.as_view(), name="negotiation-active-list"),
    path("games/<int:game_id>/negotiations/start-irrigation/", views.StartIrrigationNegotiationView.as_view(), name="negotiation-start-irrigation"),
    path("games/<int:game_id>/negotiations/<int:session_id>/chat/", views.NegotiationChatView.as_view(), name="negotiation-chat"),
    # Disaster relief application
    path("games/<int:game_id>/disaster-relief/", views.DisasterReliefView.as_view(), name="game-disaster-relief"),
    # Autumn remit ratio adjustment (九月专用)
    path("games/<int:game_id>/remit-ratio/", views.AdjustRemitRatioView.as_view(), name="game-remit-ratio"),
    # Emergency grain actions
    path("games/<int:game_id>/emergency/prefecture-relief/", views.EmergencyPrefectureReliefView.as_view(), name="emergency-prefecture-relief"),
    path("games/<int:game_id>/emergency/borrow-neighbor/", views.EmergencyBorrowNeighborView.as_view(), name="emergency-borrow-neighbor"),
    path("games/<int:game_id>/emergency/gentry-relief/", views.EmergencyGentryReliefView.as_view(), name="emergency-gentry-relief"),
    path("games/<int:game_id>/emergency/force-levy/", views.EmergencyForceLevyView.as_view(), name="emergency-force-levy"),
    path("games/<int:game_id>/emergency/debug-toggle/", views.EmergencyDebugToggleView.as_view(), name="emergency-debug-toggle"),
    # Officialdom
    path("games/<int:game_id>/officialdom/", views.OfficialdomView.as_view(), name="game-officialdom"),
    path("games/<int:game_id>/career/", views.CareerView.as_view(), name="game-career"),
    path("games/<int:game_id>/promotion-action/", views.PromotionActionView.as_view(), name="game-promotion-action"),
    path("games/<int:game_id>/new-term/", views.NewTermView.as_view(), name="game-new-term"),
    # Neighbor counties
    path("games/<int:game_id>/neighbors/precompute/", views.NeighborPrecomputeView.as_view(), name="neighbor-precompute"),
    path("games/<int:game_id>/neighbors/", views.NeighborListView.as_view(), name="neighbor-list"),
    path("games/<int:game_id>/neighbors/<int:neighbor_id>/", views.NeighborDetailView.as_view(), name="neighbor-detail"),
    path("games/<int:game_id>/neighbors/<int:neighbor_id>/events/", views.NeighborEventsView.as_view(), name="neighbor-events"),
    path("games/<int:game_id>/neighbors/<int:neighbor_id>/summary-v2/", views.NeighborSummaryV2View.as_view(), name="neighbor-summary-v2"),
    # Prefecture (知府) endpoints
    path("prefecture/create/", views_prefecture.PrefectureCreateView.as_view(), name="prefecture-create"),
    path("prefecture/<int:game_id>/", views_prefecture.PrefectureOverviewView.as_view(), name="prefecture-overview"),
    path("prefecture/<int:game_id>/advance/", views_prefecture.PrefectureAdvanceView.as_view(), name="prefecture-advance"),
    path("prefecture/<int:game_id>/precompute/", views_prefecture.PrefecturePrecomputeView.as_view(), name="prefecture-precompute"),
    path("prefecture/<int:game_id>/counties/", views_prefecture.PrefectureCountyListView.as_view(), name="prefecture-counties"),
    path("prefecture/<int:game_id>/counties/<int:unit_id>/", views_prefecture.PrefectureCountyDetailView.as_view(), name="prefecture-county-detail"),
    path("prefecture/<int:game_id>/personnel/", views_prefecture.PrefecturePersonnelView.as_view(), name="prefecture-personnel"),
    path("prefecture/<int:game_id>/quota/", views_prefecture.PrefectureQuotaView.as_view(), name="prefecture-quota"),
    path("prefecture/<int:game_id>/directive/", views_prefecture.PrefectureDirectiveView.as_view(), name="prefecture-directive"),
    path("prefecture/<int:game_id>/inspect/", views_prefecture.PrefectureInspectView.as_view(), name="prefecture-inspect"),
    path("prefecture/<int:game_id>/invest/", views_prefecture.PrefectureInvestView.as_view(), name="prefecture-invest"),
    path("prefecture/<int:game_id>/talent/", views_prefecture.PrefectureTalentView.as_view(), name="prefecture-talent"),
    path("prefecture/<int:game_id>/judicial/", views_prefecture.PrefectureJudicialView.as_view(), name="prefecture-judicial"),
    path("prefecture/<int:game_id>/judicial/decide/", views_prefecture.PrefectureJudicialDecideView.as_view(), name="prefecture-judicial-decide"),
]
