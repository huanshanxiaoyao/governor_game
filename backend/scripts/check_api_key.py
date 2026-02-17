"""Verify LLM API keys are working."""

import os
import time

from openai import OpenAI
from dotenv import load_dotenv

os.environ['HTTP_PROXY'] = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:7897'

load_dotenv()

PROVIDERS = {
    'deepseek': {
        'base_url': os.getenv('DEEPSEEK_URL', 'https://api.deepseek.com') + '/v1',
        'api_key': os.getenv('DEEPSEEK_KEY', ''),
        'model': 'deepseek-chat',
    },
    'qwen': {
        'base_url': os.getenv('QIANWEN_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1'),
        'api_key': os.getenv('QIANWEN_KEY', ''),
        'model': 'qwen-plus',
    },
    'openai': {
        'base_url': 'https://api.openai.com/v1',
        'api_key': os.getenv('OPENAI_KEY', ''),
        'model': 'gpt-4o',
    },
}
print(PROVIDERS)

def test_provider(name, cfg):
    print(f"\n{'='*50}")
    print(f"Provider: {name}")
    print(f"  base_url: {cfg['base_url']}")
    print(f"  model:    {cfg['model']}")
    print(f"  api_key:  {cfg['api_key'][:8]}...{cfg['api_key'][-4:]}" if cfg['api_key'] else "  api_key:  (empty)")

    if not cfg['api_key']:
        print("  SKIPPED - no key")
        return

    client = OpenAI(base_url=cfg['base_url'], api_key=cfg['api_key'], timeout=30.0)

    try:
        start = time.time()
        resp = client.chat.completions.create(
            model=cfg['model'],
            messages=[{'role': 'user', 'content': '用中文说你好，只需要一句话。'}],
            max_tokens=64,
        )
        elapsed = time.time() - start
        content = resp.choices[0].message.content.strip()
        print(f"  OK ({elapsed:.2f}s): {content}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")


if __name__ == '__main__':
    for name, cfg in PROVIDERS.items():
        test_provider(name, cfg)
    print(f"\n{'='*50}")
    print("Done.")
