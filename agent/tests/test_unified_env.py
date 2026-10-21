from pathlib import Path
import unittest, tempfile, os
from unittest.mock import patch
from agent.gateway import Settings

class UnifiedEnvTests(unittest.TestCase):
    def tree(self, root):
        package = root / 'agent'
        source = package / 'src/agent/gateway.py'
        source.parent.mkdir(parents=True)
        (package / 'pyproject.toml').touch()
        (root / 'mysite').mkdir()
        (root / 'mysite/manage.py').touch()
        return source

    def test_nested_agent_reads_backend_env_not_stale_nested_env(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = self.tree(root)
            (root / '.env').write_text('TAVILY_API_KEY=unified\nAGENT_TIMEOUT=42\n')
            (root / 'agent/.env').write_text('TAVILY_API_KEY=stale\n')
            with patch('agent.gateway.__file__', str(source)), patch.dict(os.environ, {}, clear=True):
                config = Settings.from_env()
                self.assertEqual(config.tavily_api_key, 'unified')
                self.assertEqual(config.timeout, 42)
                self.assertNotIn('TAVILY_API_KEY', os.environ)

    def test_process_and_explicit_path_take_precedence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = self.tree(root)
            (root / '.env').write_text('TAVILY_API_KEY=unified\n')
            explicit = root / 'custom.env'; explicit.write_text('TAVILY_API_KEY=explicit\n')
            with patch('agent.gateway.__file__', str(source)), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(Settings.from_env(env_file=explicit).tavily_api_key, 'explicit')
            with patch('agent.gateway.__file__', str(source)), patch.dict(os.environ, {'TAVILY_API_KEY':'process'}, clear=True):
                self.assertEqual(Settings.from_env().tavily_api_key, 'process')

    def test_standalone_checkout_keeps_local_env(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'pyproject.toml').touch()
            (root/'.env').write_text('TAVILY_API_KEY=standalone\n')
            with patch('agent.gateway.__file__', str(root/'src/agent/gateway.py')), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(Settings.from_env().tavily_api_key, 'standalone')

if __name__ == '__main__':
    unittest.main()
