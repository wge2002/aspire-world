"""Read-only startup gate; select only an explicit paused, bound execution."""
import argparse,json
from pathlib import Path
from startup_gate import verify
if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--control',type=Path,required=True);args=parser.parse_args()
    result=verify(args.control);print(json.dumps(result,indent=2));raise SystemExit(result['state']!='passed')
