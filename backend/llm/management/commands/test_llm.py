import time

from django.core.management.base import BaseCommand

from llm.client import LLMClient
from llm.exceptions import LLMError
from llm.providers import get_provider, get_all_providers


class Command(BaseCommand):
    help = 'Test LLM provider connectivity'

    def add_arguments(self, parser):
        parser.add_argument(
            '--provider',
            type=str,
            default=None,
            help='Specific provider to test (e.g. openai, qwen, deepseek)',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            dest='test_all',
            help='Test all configured providers',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            dest='test_json',
            help='Also test JSON mode',
        )

    def handle(self, *args, **options):
        if options['test_all']:
            providers = get_all_providers()
        elif options['provider']:
            providers = {options['provider']: get_provider(options['provider'])}
        else:
            config = get_provider()
            providers = {config.name: config}

        for name, config in providers.items():
            self._test_provider(name, config, test_json=options['test_json'])

    def _test_provider(self, name, config, test_json=False):
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Testing provider: {name}")
        self.stdout.write(f"  base_url: {config.base_url}")
        self.stdout.write(f"  model:    {config.default_model}")
        api_key_display = config.api_key[:8] + '...' if config.api_key else '(empty)'
        self.stdout.write(f"  api_key:  {api_key_display}")

        if not config.api_key:
            self.stdout.write(self.style.WARNING(
                f"  SKIPPED — no API key configured for '{name}'"
            ))
            return

        client = LLMClient(config=config)

        # Basic chat test
        self.stdout.write(f"\n  [Chat test]")
        try:
            start = time.time()
            response = client.chat(
                [{'role': 'user', 'content': '用中文说你好，只需要一句话。'}],
                max_tokens=64,
            )
            elapsed = time.time() - start
            self.stdout.write(self.style.SUCCESS(
                f"  OK ({elapsed:.2f}s): {response.strip()}"
            ))
        except LLMError as e:
            self.stdout.write(self.style.ERROR(f"  FAILED: {e}"))
            return

        # JSON mode test
        if test_json:
            self.stdout.write(f"\n  [JSON mode test]")
            try:
                start = time.time()
                result = client.chat_json(
                    [{'role': 'user', 'content': '返回一个JSON对象，包含greeting字段，值为中文问候语。'}],
                    max_tokens=64,
                )
                elapsed = time.time() - start
                self.stdout.write(self.style.SUCCESS(
                    f"  OK ({elapsed:.2f}s): {result}"
                ))
            except LLMError as e:
                self.stdout.write(self.style.ERROR(f"  FAILED: {e}"))
