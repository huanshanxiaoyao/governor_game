from django.db import migrations


def create_admin_units(apps, schema_editor):
    """为所有现有 GameState 创建对应的 AdminUnit（县级），回填 player_unit FK"""
    GameState = apps.get_model('game', 'GameState')
    AdminUnit = apps.get_model('game', 'AdminUnit')

    units_to_create = []
    games = list(GameState.objects.all())

    # 批量创建 AdminUnit
    for gs in games:
        units_to_create.append(AdminUnit(
            game=gs,
            unit_type='COUNTY',
            unit_data=gs.county_data,
            is_player_controlled=True,
        ))
    AdminUnit.objects.bulk_create(units_to_create)

    # 回填 player_unit（按 game_id 匹配）
    unit_map = {u.game_id: u for u in AdminUnit.objects.filter(unit_type='COUNTY')}
    for gs in games:
        unit = unit_map.get(gs.id)
        if unit:
            gs.player_unit = unit
            gs.player_role = 'COUNTY_MAGISTRATE'
    GameState.objects.bulk_update(games, ['player_unit', 'player_role'])


def reverse_admin_units(apps, schema_editor):
    AdminUnit = apps.get_model('game', 'AdminUnit')
    GameState = apps.get_model('game', 'GameState')
    GameState.objects.all().update(player_unit=None, player_role='COUNTY_MAGISTRATE')
    AdminUnit.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0015_adminunit_gamestate_player_fields'),
    ]

    operations = [
        migrations.RunPython(create_admin_units, reverse_code=reverse_admin_units),
    ]
