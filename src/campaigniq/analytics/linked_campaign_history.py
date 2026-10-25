"""Source-verified campaign history reader. Never writes or renames campaigns."""
from dataclasses import replace
import json

from campaigniq.historical_backfill_continuity_links import build_links, linked_campaign_nodes, summarize_records, verify_partition
from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.persistence.realized_attribution_store import deserialize_realized_attributions

CONTINUITY_KEY = 'historical-continuity-v1.json'


class LinkedCampaignHistory:
    def __init__(self, graph, artifacts, boundary_plan):
        if graph.get('format') != 'campaigniq.period_scoped_continuity_links' or graph.get('version') != 1:
            raise ValueError('Unsupported campaign continuity format.')
        if graph.get('scope') != 'brokerage_equity_and_options':
            raise ValueError('Unsupported campaign continuity scope.')
        # Rebuild from the exact published artifacts, rather than trusting cached
        # links, totals, record indices or a successful rehearsal flag.
        if build_links(boundary_plan, artifacts) != graph:
            raise ValueError('Campaign continuity differs from its source artifacts.')
        verify_partition(graph)
        self.graph = json.loads(canonical(graph))
        self.artifacts = dict(artifacts)

    def selection(self, campaign_id):
        month, cid = campaign_id.split('/', 1)
        seed = [month, cid]
        if seed not in self.graph['campaign_nodes']:
            return None
        nodes = linked_campaign_nodes(self.graph, [seed])
        summary = summarize_records(self.graph, nodes)
        if not summary['selected_history_monetary_coverage_complete']:
            raise ValueError('Linked campaign contains shared or unassigned broker results.')
        wanted = set(summary['record_ids'])
        records = []
        parsed = {}
        for row in self.graph['realized_records']:
            if row['record_id'] not in wanted:
                continue
            key = row['source_artifact']
            if key not in parsed:
                parsed[key] = deserialize_realized_attributions(self.artifacts[key]).attributions
            item = parsed[key][row['record_index']]
            records.append(replace(item, allocations=tuple(replace(a,
                campaign_id=key[:7] + '/' + a.campaign_id if a.campaign_id else None)
                for a in item.allocations)))
        return dict(records=tuple(records), campaign_nodes=tuple(tuple(n) for n in nodes),
            record_count=summary['record_count'], totals=summary['totals'])

    def entries(self, evidence, selection):
        nodes = set(selection['campaign_nodes'])
        return [row for row in evidence if (row['period_start'][:7], row.get('campaign_id')) in nodes]


def load_linked_history(storage):
    """Absent graph preserves current behavior; present invalid graph raises.

    The atomic publication archive retains the reviewed boundary plan. Requiring
    it binds the reader to the same original evidence used by the rehearsal.
    """
    if not storage.exists(CONTINUITY_KEY):
        return None
    graph = json.loads(storage.read_text(CONTINUITY_KEY))
    checksum = digest(canonical(graph))
    plans = []
    for key in storage.list_keys(prefix='combined-rehearsal-', suffix='.json'):
        archive = json.loads(storage.read_text(key))
        if archive.get('format') == 'campaigniq.combined_rehearsal_archive' and archive.get('continuity_graph_sha256') == checksum:
            plan = archive.get('boundary_plan')
            if plan is not None and digest(canonical(plan)) == graph.get('source_boundary_plan_sha256'):
                plans.append(plan)
    if not plans or any(plan != plans[0] for plan in plans):
        raise ValueError('Reviewed campaign boundary evidence is unavailable or ambiguous.')
    plan = plans[0]
    keys = set(plan['existing_artifact_sha256']) | set(graph['historical_candidate_sha256'])
    artifacts = {key: storage.read_text(key) for key in keys}
    return LinkedCampaignHistory(graph, artifacts, plan)
