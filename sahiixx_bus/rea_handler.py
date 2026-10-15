"""
REAL bus handler for rea.analysis.completed
"""
import asyncio
from datetime import datetime

class REABusHandler:
    def calculate_risk(self, strings):
        critical = []
        endpoints = []
        dubai = []
        for s in strings:
            cats = s.get('categories', [])
            if any(k in cats for k in ['GENERIC_API_KEY', 'BAYUT', 'POTENTIAL_KEY']):
                critical.append(s)
            if 'URL' in cats or 'ENDPOINT' in cats:
                endpoints.append(s)
            if 'DUBAI_RE' in cats or 'BAYUT' in cats:
                dubai.append(s)
        return {
            'critical_secrets': critical[:10],
            'endpoints': endpoints[:20],
            'dubai_re': dubai[:20],
            'risk_level': 'CRITICAL' if critical else 'MEDIUM' if endpoints else 'LOW'
        }
    
    async def handle(self, event):
        payload = event.get('payload', {})
        analysis_id = payload.get('id', 'unknown')
        strings = payload.get('strings', [])
        evidence = payload.get('evidence', [])
        risk = self.calculate_risk(strings)
        
        memory_event = {
            'analysis_id': analysis_id,
            'sha256': payload.get('sha256',''),
            'strings_count': len(strings),
            'evidence': evidence[:10]
        }
        
        swarm_task = {
            'analysis_id': analysis_id,
            'strings': strings[:100],
            'evidence': evidence[:20],
            'risk': risk,
            'competitor': 'bayut.com' if 'bayut' in str(strings).lower() else 'unknown'
        }
        
        return {
            'handled': True,
            'analysis_id': analysis_id,
            'fanout': {'memory': memory_event, 'swarm': swarm_task},
            'next': ['titans-memory', 'swarm', 'revenue-os']
        }

handler = REABusHandler()
