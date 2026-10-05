import json
import gzip
import base64
import boto3
import os

sqs = boto3.client('sqs')
QUEUE_URL = os.environ['QUEUE_URL']

def lambda_handler(event, context):
    # CloudWatch Logs 구독 필터는 base64 + gzip으로 압축해서 보냄
    compressed_payload = base64.b64decode(event['awslogs']['data'])
    uncompressed_payload = gzip.decompress(compressed_payload)
    log_data = json.loads(uncompressed_payload)
    if log_data.get('messageType') == 'CONTROL_MESSAGE':
        return {'statusCode': 200}
    group = log_data['logGroup']
    kind = 'vpc_flow' if group.endswith('/vpc-flow-logs') else 'alb' if group.endswith('/alb/access') else 'nginx'

    # 로그 이벤트 하나하나를 SQS로 전송
    for log_event in log_data['logEvents']:
        message = {
            'event_id': log_event['id'],
            'log_type': kind,
            'logGroup': log_data['logGroup'],
            'logStream': log_data['logStream'],
            'timestamp': log_event['timestamp'],
            'message': log_event['message']
        }
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(message)
        )

    return {'statusCode': 200}
