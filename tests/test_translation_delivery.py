"""Approved preparation seam: real upstream and storage, fake external I/O only."""
from types import SimpleNamespace

import pytest

from coworker.providers.base import AssistantTurn
from coworker.server.youtube import HostManuscripts
from shorts_fixture import RegularVideos
from test_workbench_library import FakeCaptions, candidate
from translation_fixture import EchoModel
from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench.library import LibraryService, PreparationService
from youtube_strataread.workbench.workspace import LocalWorkspace


@pytest.mark.parametrize('source', ['Hello world.', '這是中文。', '中文 with English.'])
def test_complete_upstream_translation_is_independently_delivered(tmp_path, source):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta('drain_paused', '0')
    ws.add_candidate(candidate('one'))
    returned = '  完整译文　\n\n末尾。\n\n'
    calls = []
    def complete(model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return AssistantTurn(text=returned if len(calls) == 3 else source)
    manager = SimpleNamespace(model='selected-model', provider_complete=complete)
    service = PreparationService(ws, FakeCaptions(SubtitleResult('one', 'Title', 'en', False,
        f'1\n00:00:00,000 --> 00:00:02,000\n{source}\n')), HostManuscripts(manager, ws), RegularVideos())
    assert service.run_next()
    assert len(calls) == 3
    assert calls[0][1][0]['content'].startswith('You are an expert linguist')
    assert all(call[0] == 'selected-model' for call in calls)
    library = LibraryService(ws, service)
    assert library.list_assets(documents_only=True)['total'] == 1
    document = library.document('one', kind='translation')
    assert document['markdown'] == returned
    assert document['version'] == 1
    assert library.inspect('one')['translation_version'] == 1
    assert library.inspect('one')['manuscript_version'] is None
    assert library.inspect('one')['preparation_state'] == 'translation_ready'
    reopened = LocalWorkspace.open(tmp_path)
    reopened.recover_interrupted_preparations()
    assert reopened.document('one', kind='translation')['markdown'] == returned
    assert reopened.asset('one')['preparation_state'] == 'translation_ready'
    assert not service.run_next()
