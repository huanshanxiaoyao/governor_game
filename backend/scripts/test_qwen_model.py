"""
对比不同 Qwen 模型在邻县施政决策场景下的表现。

用法（Docker 容器内）:
  python scripts/test_qwen_model.py --game-id 32
  python scripts/test_qwen_model.py --game-id 32 --models qwen-plus,qwen3.5-plus,qwen-turbo
  python scripts/test_qwen_model.py --game-id 32 --print-prompt
  python scripts/test_qwen_model.py --game-id 32 --print-response
"""

import argparse
import json
import os
import sys
import time

# Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import django
django.setup()

from openai import OpenAI
from game.models import GameState
from game.services.ai_governor import AIGovernorService
from game.services.constants import GOVERNOR_STYLES


def build_real_prompts(game_id, print_prompt=False):
    """从真实游戏数据构建所有邻县的 prompt"""
    game = GameState.objects.get(id=game_id)
    neighbors = list(game.neighbors.all())
    if not neighbors:
        print(f"游戏 #{game_id} 没有邻县数据")
        sys.exit(1)

    season = game.current_season
    print(f"=== 加载游戏 #{game_id}, 当前第{season}季 ===")

    prompts = []
    for n in neighbors:
        profile = AIGovernorService._ensure_profile(n)
        ctx = AIGovernorService._build_context(n, n.county_data, season, profile)

        from llm.prompts import PromptRegistry
        system_prompt, user_prompt = PromptRegistry.render(
            'ai_governor_decision', **ctx)

        style_name = GOVERNOR_STYLES.get(n.governor_style, {}).get('name', n.governor_style)
        print(f"  {n.county_name} ({n.governor_name}, {style_name})")

        if print_prompt:
            print(f"\n{'='*60} SYSTEM PROMPT [{n.county_name}] {'='*60}")
            print(system_prompt)
            print(f"\n{'='*60} USER PROMPT [{n.county_name}] {'='*60}")
            print(user_prompt)
            print(f"{'='*60}\n")

        prompts.append({
            'county_name': n.county_name,
            'governor_name': n.governor_name,
            'style_name': style_name,
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
        })

    print()
    return prompts


def _format_tax(tax):
    """智能格式化税率：兼容 0.12 和 12 两种返回格式"""
    if tax is None or tax == '-':
        return '-'
    try:
        val = float(tax)
        if val > 1:
            # LLM 返回的是百分数（如 12、15）
            return f"{val:.0f}%"
        else:
            # 正常小数（如 0.12）
            return f"{val:.0%}"
    except (ValueError, TypeError):
        return str(tax)


def test_model(model_name, prompts, api_key, base_url, max_tokens, timeout,
               print_response=False):
    """测试一个模型对所有 prompt 的表现"""
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=float(timeout))

    print(f"--- 测试模型: {model_name} (max_tokens={max_tokens}, timeout={timeout}s) ---")
    results = []

    for p in prompts:
        row = {
            'county': p['county_name'],
            'governor': p['governor_name'],
            'style': p['style_name'],
            'model': model_name,
        }

        start = time.time()
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {'role': 'system', 'content': p['system_prompt']},
                    {'role': 'user', 'content': p['user_prompt']},
                ],
                response_format={'type': 'json_object'},
                temperature=0.7,
                max_tokens=max_tokens,
            )
            elapsed = time.time() - start
            content = resp.choices[0].message.content
            total_tokens = getattr(resp.usage, 'total_tokens', 0)
            prompt_tokens = getattr(resp.usage, 'prompt_tokens', 0)
            completion_tokens = getattr(resp.usage, 'completion_tokens', 0)
            finish_reason = resp.choices[0].finish_reason

            row['time'] = elapsed
            row['total_tokens'] = total_tokens
            row['prompt_tokens'] = prompt_tokens
            row['completion_tokens'] = completion_tokens
            row['finish_reason'] = finish_reason
            row['raw_content'] = content

            if print_response:
                print(f"\n  >>> RAW RESPONSE [{p['county_name']}] "
                      f"(finish_reason={finish_reason}):")
                print(f"  {content}")
                print()

            # 解析 JSON
            try:
                data = json.loads(content)
                row['json_ok'] = True
                row['analysis'] = data.get('analysis', '')
                row['reasoning'] = data.get('reasoning', '')
                decisions = data.get('decisions', {})
                if isinstance(decisions, dict):
                    # 新格式：investments 数组
                    investments = decisions.get('investments', [])
                    # 兼容旧格式：单个 investment 字段
                    if not investments and decisions.get('investment'):
                        inv = decisions['investment']
                        if inv and str(inv).lower() != 'null':
                            investments = [{'action': inv}]
                    row['investments'] = investments
                    row['tax_rate'] = decisions.get('tax_rate')
                    row['medical_level'] = decisions.get('medical_level')
                    has_inv = len(investments) > 0
                    has_tax = row['tax_rate'] is not None
                    has_med = row['medical_level'] is not None
                    row['decisions_complete'] = has_inv and has_tax and has_med
                    row['has_investment'] = has_inv
                    row['investment_count'] = len(investments)
                else:
                    row['decisions_complete'] = False
                    row['has_investment'] = False
                    row['investment_count'] = 0
            except (json.JSONDecodeError, TypeError) as e:
                row['json_ok'] = False
                row['json_error'] = str(e)
                row['decisions_complete'] = False
                row['has_investment'] = False

        except Exception as e:
            elapsed = time.time() - start
            row['time'] = elapsed
            row['error'] = f"{type(e).__name__}: {e}"
            row['json_ok'] = False
            row['decisions_complete'] = False
            row['has_investment'] = False

        # 打印单行结果
        _print_row(row)
        results.append(row)

    print()
    return results


def _print_row(row):
    """打印单次测试结果"""
    county = row['county']
    t = row.get('time', 0)
    tokens = row.get('total_tokens', 0)
    comp_tokens = row.get('completion_tokens', 0)
    finish = row.get('finish_reason', '?')

    if row.get('error'):
        print(f"  {county}: {t:.1f}s | ERROR: {row['error']}")
        return

    json_status = "OK" if row.get('json_ok') else "FAIL"

    if row.get('json_ok'):
        investments = row.get('investments', [])
        inv_count = len(investments)
        inv_actions = ', '.join(
            i.get('action', '?') if isinstance(i, dict) else str(i)
            for i in investments
        ) if investments else '-'
        tax = _format_tax(row.get('tax_rate'))
        med = row.get('medical_level', '-')
        analysis = row.get('analysis', '')[:80]
        print(f"  {county}: {t:.1f}s | {tokens}tk(out:{comp_tokens}) | "
              f"finish:{finish} | JSON:{json_status} "
              f"| inv({inv_count}):[{inv_actions}] tax:{tax} med:{med}")
        print(f"    analysis: \"{analysis}\"")
    else:
        err = row.get('json_error', 'unknown')[:80]
        raw_tail = row.get('raw_content', '')[-100:] if row.get('raw_content') else ''
        print(f"  {county}: {t:.1f}s | {tokens}tk(out:{comp_tokens}) | "
              f"finish:{finish} | JSON:FAIL | {err}")
        if raw_tail:
            print(f"    response tail: ...{raw_tail}")


def print_summary(all_results, models):
    """打印汇总对比表"""
    print("=" * 100)
    print("=== 汇总 ===")
    print(f"{'模型':<20} | {'平均耗时':>8} | {'平均tokens':>10} | {'平均out':>8} | "
          f"{'JSON成功':>8} | {'决策完整':>8} | {'平均投资数':>10}")
    print("-" * 100)

    for model in models:
        rows = [r for r in all_results if r['model'] == model]
        n = len(rows)
        if n == 0:
            continue

        avg_time = sum(r.get('time', 0) for r in rows) / n
        avg_tokens = sum(r.get('total_tokens', 0) for r in rows) / n
        avg_comp = sum(r.get('completion_tokens', 0) for r in rows) / n
        json_ok = sum(1 for r in rows if r.get('json_ok'))
        decisions_ok = sum(1 for r in rows if r.get('decisions_complete'))
        avg_inv = sum(r.get('investment_count', 0) for r in rows) / n

        print(f"{model:<20} | {avg_time:>7.1f}s | {avg_tokens:>10.0f} | {avg_comp:>8.0f} | "
              f"{json_ok:>4}/{n:<3} | {decisions_ok:>4}/{n:<3} | {avg_inv:>10.1f}")

    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(description='对比 Qwen 模型在邻县施政决策场景的表现')
    parser.add_argument('--game-id', type=int, required=True, help='游戏ID')
    parser.add_argument('--models', type=str, default='qwen-plus,qwen3.5-plus',
                        help='逗号分隔的模型列表 (默认: qwen-plus,qwen3.5-plus)')
    parser.add_argument('--max-tokens', type=int, default=1024,
                        help='max_tokens (默认: 1024)')
    parser.add_argument('--timeout', type=int, default=30,
                        help='单次请求超时秒数 (默认: 30)')
    parser.add_argument('--print-prompt', action='store_true',
                        help='打印完整的 system/user prompt')
    parser.add_argument('--print-response', action='store_true',
                        help='打印完整的 LLM 原始响应')
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(',') if m.strip()]
    api_key = os.getenv('QWEN_API_KEY', '')
    base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'

    if not api_key:
        print("ERROR: QWEN_API_KEY 环境变量未设置")
        sys.exit(1)

    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    print(f"模型列表: {models}")
    print(f"max_tokens: {args.max_tokens}, timeout: {args.timeout}s")
    print()

    # 构建真实 prompt
    prompts = build_real_prompts(args.game_id, print_prompt=args.print_prompt)

    # 测试每个模型
    all_results = []
    for model in models:
        results = test_model(model, prompts, api_key, base_url,
                             args.max_tokens, args.timeout,
                             print_response=args.print_response)
        all_results.extend(results)

    # 汇总
    print_summary(all_results, models)


if __name__ == '__main__':
    main()
