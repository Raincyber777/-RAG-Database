"""Small live-model smoke check using public sample text, never user classroom data."""
from dataclasses import replace

from agent.gateway import Settings
from agent.loop import QA_TOOLS
from agent.runtime import open_agent
from agent.schemas import Chunk, Material, UserContext


class SampleBackend:
    def material(self, context):
        return Material(title='示例微积分', course='数学', text='导数描述函数在某一点的瞬时变化率。')

    def retrieve(self, question, context, limit=4):
        return [Chunk(text='导数描述函数在某一点的瞬时变化率。', ref='示例讲义 第1页', score=.95)]

    def profile(self, context):
        return None


def main():
    config = replace(Settings.from_env(), enable_search=False)
    if not config.api_key:
        raise SystemExit('Model key not configured')
    with open_agent(SampleBackend(), settings=config, tool_names=QA_TOOLS, max_model_calls=5) as agent:
        result = None
        deltas = 0
        for event in agent.stream('请检索课堂资料，筛选证据后解释导数的含义。',
                                  context=UserContext('smoke', 'local-test', 'sample'), thread_id='smoke'):
            if event['event'] == 'status':
                print('Stage:', event['data'].get('stage'), 'Round:', event['data'].get('round', ''), flush=True)
            elif event['event'] == 'delta':
                deltas += 1
            elif event['event'] == 'done':
                result = event['data']
    print('Model calls:', result['model_call_count'])
    print('Answer delta events:', deltas)
    print('Validated sources:', len(result['output'].get('sources', [])))
    print('Degraded:', result['output'].get('degraded', False))
    if result['output'].get('degraded') or not result['output'].get('sources') or result['model_call_count'] < 3 or not deltas:
        raise SystemExit(1)
    print('LangChain model/tool loop: OK')


if __name__ == '__main__':
    main()
