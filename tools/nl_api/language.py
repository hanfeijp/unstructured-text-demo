# Copyright 2016 Google, Inc
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Analyzes text using the Google Cloud Text API."""

import logging
import multiprocessing

from googleapiclient import discovery
from googleapiclient import http
import httplib2shim
from oauth2client.client import GoogleCredentials


# Global variable to cache the service object used to make API calls.
_service = None


def get_service():
    global _service
    if not _service:
        credentials = GoogleCredentials.get_application_default(
        ).create_scoped(['https://www.googleapis.com/auth/cloud-platform'])
        http = httplib2shim.Http()
        credentials.authorize(http)
        _service = discovery.build('language', 'v1beta1', http=http)
    return _service


def _with_retries(f, name, num_retries=15):
    """Retry the given request function.

    This is currently necessary because a batch request object doesn't retry
    requests.
    """
    for retry_num in range(num_retries + 1):
        try:
            result = f()
            break
        except http.HttpError as e:
            if e.resp.status >= 500:
                logging.warning(
                    '{}: Server error on retry {}: {}.\nSleeping..'.format(
                        name, retry_num, e))
                time.sleep(random.random() * 2 ** retry_num)

            elif e.resp.status == 429:
                logging.warning(
                    '{}: Quota error on retry {}: {}.\nSleeping.. '.format(
                        name, retry_num, e))
                time.sleep(random.random() * 2 ** retry_num)

            elif e.resp.status >= 400:
                logging.error(
                    '{}: User error on retry {}: {}'.format(
                        name, retry_num, e))
                raise

        except urllib3.exceptions.HTTPError:
            pass

    else:
        logging.error('{}: Retries exhausted. Returning None'.format(name))
        return None

    return result


def _annotate_text_request(
        text, encoding=None, extract_syntax=False, extract_entities=False,
        extract_document_sentiment=False):
    body = {
        'document': {
            'type': 'PLAIN_TEXT',
            'language': 'en',
            'content': text,
        },
        'features': {
            'extractSyntax': extract_syntax,
            'extractEntities': extract_entities,
            'extractDocumentSentiment': extract_document_sentiment,
        },
        'encoding_type': encoding,
    }

    service = get_service()

    return service.documents().annotateText(body=body)


def annotate_text(text, encoding=None, extract_syntax=False,
                  extract_entities=False, extract_document_sentiment=False):
    request = _annotate_text_request(
        text, encoding, extract_syntax, extract_entities,
        extract_document_sentiment)
    response = None
    try:
        response = request.execute(num_retries=15)
    # This only happens for 400 errors
    except http.HttpError:
        logging.exception('Http error while annotating text.')
        return {}

    return response


def _batch_accumulate(queue):
    def callback(request_id, response, exception):
        queue.put((request_id, response, exception and str(exception)))
    return callback


def annotate_text_batch(texts, *args, **kwargs):
    num_texts = len(texts)
    queue = multiprocessing.Queue(num_texts)
    responses = [None] * num_texts

    batch = http.BatchHttpRequest(
        callback=_batch_accumulate(queue),
        batch_uri='https://language.googleapis.com/batch')
    for i, text in enumerate(texts):
        batch.add(
            _annotate_text_request(text, *args, **kwargs),
            request_id=str(i))

    try:
        _with_retries(batch.execute, 'NL Batch')
    # This only happens for 400 errors
    except http.HttpError:
        return responses

    for _ in range(num_texts):
        reqid, resp, exc = queue.get()
        reqid = int(reqid)
        if exc:
            # Fall back to doing it serially
            logging.warning('Error sending batch request ({}).\n'
                            'Falling back to serial'.format(exc))
            responses[reqid] = annotate_text(texts[reqid], *args, **kwargs)
        else:
            responses[reqid] = resp

    return responses
