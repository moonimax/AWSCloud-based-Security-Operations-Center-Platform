"""Explicit user-run paid smoke call. No source logs sent to the model."""
import os
import sys
from planner import plan_search

def main():
    model=os.environ.get('BEDROCK_MODEL_ID','').strip()
    if not model:
        print('BEDROCK_MODEL_ID is empty; keyword mode is configured.',file=sys.stderr)
        return 1
    print('Model:',model)
    print('Region:',os.environ.get('AWS_REGION','ap-northeast-2'))
    try:
        plan,engine=plan_search('최근 15분 ALB 500 로그를 보여줘')
    except Exception as exc:
        response=getattr(exc,'response',{})
        code=response.get('Error',{}).get('Code',type(exc).__name__)
        print('Bedrock check failed:',code,file=sys.stderr)
        print('Check regional inference profile availability, IAM InvokeModel for profile + destination models, SCP and credential expiry.',file=sys.stderr)
        return 1
    if engine!='bedrock':
        print('Not a Bedrock response.',file=sys.stderr);return 1
    print('Bedrock responded and the search plan passed validation:',plan)
    return 0

if __name__=='__main__':sys.exit(main())
