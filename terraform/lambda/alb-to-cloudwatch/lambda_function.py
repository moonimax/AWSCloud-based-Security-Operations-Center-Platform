"""Stream ALB gzip files to CloudWatch; keep originals in S3 for replay.

At-least-once: retried files may duplicate events. No exactly-once claim.
"""
import gzip
import os
from datetime import datetime
from urllib.parse import unquote_plus
import boto3

s3 = boto3.client('s3')
logs = boto3.client('logs')

def put(events):
    if not events:
        return
    response = logs.put_log_events(
        logGroupName=os.environ['LOG_GROUP'], logStreamName=os.environ['LOG_STREAM'],
        logEvents=sorted(events, key=lambda e:e['timestamp']))
    if response.get('rejectedLogEventsInfo'):
        raise RuntimeError('CloudWatch rejected events; retain S3 object for investigation')

def lambda_handler(event, context):
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])
        if not key.endswith('.log.gz'):
            continue
        body = s3.get_object(Bucket=bucket, Key=key)['Body']
        try:
            with gzip.GzipFile(fileobj=body) as source:
                batch=[]; size=0; earliest=None
                for encoded in source:
                    line=encoded.decode('utf-8').rstrip('\n')
                    if not line:continue
                    timestamp=int(datetime.fromisoformat(line.split()[1].replace('Z','+00:00')).timestamp()*1000)
                    length=len(line.encode('utf-8'))+26
                    if length>1000000:raise ValueError('Oversized ALB event')
                    if batch and (size+length>1000000 or len(batch)>=10000 or abs(timestamp-earliest)>=86400000):
                        put(batch);batch=[];size=0;earliest=None
                    earliest=timestamp if earliest is None else min(earliest,timestamp)
                    batch.append({'timestamp':timestamp,'message':line});size+=length
                put(batch)
        finally:
            body.close()
    return {'statusCode':200}
