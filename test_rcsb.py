#!/usr/bin/env python3
"""Test script for RCSB API"""
import httpx
import json

RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"

# Test sequence (GTPase HRas protein from Gallus gallus)
test_sequence = "MTEYKLVVVGAGGVGKSALTIQLIQNHFVDEYDPTIEDSYRKQVVIDGETCLLDILDTAGQEEYSAMRDQYMRTGEGFLCVFAINNTKSFEDIHQYREQIKRVKDSDDVPMVLVGNKCDLPARTVETRQAQDLARSYGIPYIETSAKTRQGVEDAFYTLVREIRQHKLRKLNPPDESGPGCMNCKCVIS"

query = {
    "query": {
        "type": "terminal",
        "service": "sequence",
        "parameters": {
            "evalue_cutoff": 1.0,
            "identity_cutoff": 0.9,
            "sequence_type": "protein",
            "value": test_sequence
        }
    },
    "return_type": "polymer_entity",
    "request_options": {
        "results_content_type": ["experimental"],
        "results_verbosity": "verbose",
        "paginate": {"start": 0, "rows": 10},
        "scoring_strategy": "sequence"
    }
}

print("Testing RCSB API...")
print(f"URL: {RCSB_SEARCH_URL}")
print(f"Query: {json.dumps(query, indent=2)}")

try:
    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        print("\nSending request...")
        response = client.post(RCSB_SEARCH_URL, json=query)
        print(f"Status code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        print(f"Response text length: {len(response.text)}")
        print(f"Response text (first 500 chars): {response.text[:500]}")
        
        if response.status_code == 200:
            if response.text:
                result = response.json()
                print(f"\nResult keys: {result.keys()}")
                if "result_set" in result:
                    print(f"Number of results: {len(result['result_set'])}")
                    if result['result_set']:
                        print(f"First result: {result['result_set'][0]}")
            else:
                print("ERROR: Response is empty!")
        else:
            print(f"ERROR: HTTP {response.status_code}")
            
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
