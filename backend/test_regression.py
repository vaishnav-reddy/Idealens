import asyncio
import sys
from main import run_analysis

async def main():
    idea = "A task management app with AI prioritization that learns from your habits and syncs across all devices, targeted at remote teams."
    print(f"Running regression test with idea:\n'{idea}'\n")
    
    try:
        result = await run_analysis(idea)
    except Exception as e:
        print(f"Error running analysis: {e}")
        sys.exit(1)
        
    competitors = result.get("competitors", [])
    print(f"\nDiscovered competitors ({len(competitors)}):")
    for comp in competitors:
        print(f"- {comp.get('name')} (URL: {comp.get('url')})")
        
    print("\nVerification Failures:")
    failures = result.get("verification_failures", [])
    for f in failures:
        print(f"- Name: {f.get('name')}, Stage: {f.get('stage')}, Reason: {f.get('reason')}")
        
    # Check assertions
    if not competitors:
        print("\nFAIL: Verified competitor list is empty!")
        sys.exit(1)
        
    expected_matches = ["asana", "clickup", "motion", "reclaim", "todoist", "quad flow"]
    found_expected = False
    
    for comp in competitors:
        name_lower = comp.get("name", "").lower()
        for expected in expected_matches:
            if expected in name_lower:
                print(f"Matched expected competitor: '{comp.get('name')}' contains '{expected}'")
                found_expected = True
                
    if found_expected:
        print("\nSUCCESS: Regression test passed!")
        sys.exit(0)
    else:
        print("\nFAIL: None of the expected competitors (Asana, ClickUp, Motion, Reclaim, Todoist, Quad Flow) were found in the results.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
