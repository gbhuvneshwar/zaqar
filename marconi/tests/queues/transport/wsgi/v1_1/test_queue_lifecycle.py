# Copyright (c) 2013 Rackspace, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License.  You may obtain a copy
# of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

import uuid

import ddt
import falcon
import six

from marconi.openstack.common import jsonutils
from marconi import tests as testing
from marconi.tests.queues.transport.wsgi import base


@ddt.ddt
class QueueLifecycleBaseTest(base.V1_1Base):

    config_file = None

    def setUp(self):
        super(QueueLifecycleBaseTest, self).setUp()

        self.queue_path = self.url_prefix + '/queues'
        self.gumshoe_queue_path = self.queue_path + '/gumshoe'
        self.fizbat_queue_path = self.queue_path + '/fizbat'
        self.fizbat_queue_path_metadata = self.fizbat_queue_path + '/metadata'

        self.headers = {
            'Client-ID': str(uuid.uuid4()),
            'X-Project-ID': '3387309841abc_'
        }

    def test_empty_project_id(self):
        headers = {
            'Client-ID': str(uuid.uuid4()),
            'X-Project-ID': ''
        }
        self.simulate_get(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

        self.simulate_put(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

        self.simulate_head(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

        self.simulate_delete(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

    @ddt.data('480924', 'foo')
    def test_basics_thoroughly(self, project_id):
        headers = {
            'Client-ID': str(uuid.uuid4()),
            'X-Project-ID': project_id
        }
        gumshoe_queue_path_metadata = self.gumshoe_queue_path + '/metadata'
        gumshoe_queue_path_stats = self.gumshoe_queue_path + '/stats'

        # Stats are empty - queue not created yet
        self.simulate_get(gumshoe_queue_path_stats, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_200)

        # Metadata not found - queue not created yet
        self.simulate_get(gumshoe_queue_path_metadata, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_404)

        # Create
        self.simulate_put(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)

        location = self.srmock.headers_dict['Location']
        self.assertEqual(location, self.gumshoe_queue_path)

        # Ensure queue existence
        self.simulate_head(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # Add metadata

        doc = '{"messages": {"ttl": 600}}'
        self.simulate_put(gumshoe_queue_path_metadata,
                          headers=headers, body=doc)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # Fetch metadata
        result = self.simulate_get(gumshoe_queue_path_metadata,
                                   headers=headers)
        result_doc = jsonutils.loads(result[0])
        self.assertEqual(self.srmock.status, falcon.HTTP_200)
        self.assertEqual(result_doc, jsonutils.loads(doc))

        # Stats empty queue
        self.simulate_get(gumshoe_queue_path_stats, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_200)

        # Delete
        self.simulate_delete(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # Get non-existent queue
        self.simulate_get(self.gumshoe_queue_path, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_404)

        # Get non-existent stats
        self.simulate_get(gumshoe_queue_path_stats, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_200)

        # Get non-existent metadata
        self.simulate_get(gumshoe_queue_path_metadata, headers=headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_404)

    def test_name_restrictions(self):
        self.simulate_put(self.queue_path + '/Nice-Boat_2',
                          headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)

        self.simulate_put(self.queue_path + '/Nice-Bo@t',
                          headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

        self.simulate_put(self.queue_path + '/_' + 'niceboat' * 8,
                          headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

    def test_project_id_restriction(self):
        muvluv_queue_path = self.queue_path + '/Muv-Luv'

        self.simulate_put(muvluv_queue_path,
                          headers={'Client-ID': str(uuid.uuid4()),
                                   'X-Project-ID': 'JAM Project' * 24})
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

        # no charset restrictions
        self.simulate_put(muvluv_queue_path,
                          headers={'Client-ID': str(uuid.uuid4()),
                                   'X-Project-ID': 'JAM Project'})
        self.assertEqual(self.srmock.status, falcon.HTTP_201)

    def test_non_ascii_name(self):
        test_params = ((u'/queues/non-ascii-n\u0153me', 'utf-8'),
                       (u'/queues/non-ascii-n\xc4me', 'iso8859-1'))

        for uri, enc in test_params:
            uri = self.url_prefix + uri

            if six.PY2:
                uri = uri.encode(enc)

            self.simulate_put(uri, headers=self.headers)
            self.assertEqual(self.srmock.status, falcon.HTTP_400)

            self.simulate_get(uri, headers=self.headers)
            self.assertEqual(self.srmock.status, falcon.HTTP_400)

            self.simulate_delete(uri, headers=self.headers)
            self.assertEqual(self.srmock.status, falcon.HTTP_400)

    def test_no_metadata(self):
        self.simulate_put(self.fizbat_queue_path, headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)

        self.simulate_put(self.fizbat_queue_path_metadata,
                          headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

        self.simulate_put(self.fizbat_queue_path_metadata, body='',
                          headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

    @ddt.data('{', '[]', '.', '  ', '')
    def test_bad_metadata(self, document):
        self.simulate_put(self.fizbat_queue_path, headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)

        self.simulate_put(self.fizbat_queue_path_metadata,
                          headers=self.headers,
                          body=document)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

    def test_too_much_metadata(self):
        self.simulate_put(self.fizbat_queue_path, headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)
        doc = '{{"messages": {{"ttl": 600}}, "padding": "{pad}"}}'

        max_size = self.transport_cfg.max_queue_metadata
        padding_len = max_size - (len(doc) - 10) + 1

        doc = doc.format(pad='x' * padding_len)

        self.simulate_put(self.fizbat_queue_path_metadata,
                          headers=self.headers,
                          body=doc)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

    def test_way_too_much_metadata(self):
        self.simulate_put(self.fizbat_queue_path, headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)
        doc = '{{"messages": {{"ttl": 600}}, "padding": "{pad}"}}'

        max_size = self.transport_cfg.max_queue_metadata
        padding_len = max_size * 100

        doc = doc.format(pad='x' * padding_len)

        self.simulate_put(self.fizbat_queue_path_metadata,
                          headers=self.headers, body=doc)
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

    def test_custom_metadata(self):
        self.simulate_put(self.fizbat_queue_path, headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)

        # Set
        doc = '{{"messages": {{"ttl": 600}}, "padding": "{pad}"}}'

        max_size = self.transport_cfg.max_queue_metadata
        padding_len = max_size - (len(doc) - 2)

        doc = doc.format(pad='x' * padding_len)
        self.simulate_put(self.fizbat_queue_path_metadata,
                          headers=self.headers,
                          body=doc)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # Get
        result = self.simulate_get(self.fizbat_queue_path_metadata,
                                   headers=self.headers)
        result_doc = jsonutils.loads(result[0])
        self.assertEqual(result_doc, jsonutils.loads(doc))
        self.assertEqual(self.srmock.status, falcon.HTTP_200)

    def test_update_metadata(self):
        xyz_queue_path = self.url_prefix + '/queues/xyz'
        xyz_queue_path_metadata = xyz_queue_path + '/metadata'

        # Create
        self.simulate_put(xyz_queue_path, headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_201)

        # Set meta
        doc1 = '{"messages": {"ttl": 600}}'
        self.simulate_put(xyz_queue_path_metadata,
                          headers=self.headers,
                          body=doc1)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # Update
        doc2 = '{"messages": {"ttl": 100}}'
        self.simulate_put(xyz_queue_path_metadata,
                          headers=self.headers,
                          body=doc2)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # Get
        result = self.simulate_get(xyz_queue_path_metadata,
                                   headers=self.headers)
        result_doc = jsonutils.loads(result[0])

        self.assertEqual(result_doc, jsonutils.loads(doc2))
        self.assertEqual(self.srmock.headers_dict['Content-Location'],
                         xyz_queue_path_metadata)

    def test_list(self):
        arbitrary_number = 644079696574693
        project_id = str(arbitrary_number)
        header = {
            'X-Project-ID': project_id
        }

        # NOTE(kgriffs): It's important that this one sort after the one
        # above. This is in order to prove that bug/1236605 is fixed, and
        # stays fixed!
        alt_project_id = str(arbitrary_number + 1)

        # List empty
        self.simulate_get(self.queue_path, headers=header)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # Payload exceeded
        self.simulate_get(self.queue_path, headers=header,
                          query_string='limit=21')
        self.assertEqual(self.srmock.status, falcon.HTTP_400)

        # Create some
        def create_queue(name, project_id, body):
            altheader = {}
            if project_id is not None:
                altheader['X-Project-ID'] = project_id
            uri = self.queue_path + '/' + name
            self.simulate_put(uri, headers=altheader)
            self.simulate_put(uri + '/metadata', headers=altheader, body=body)

        create_queue('g1', None, '{"answer": 42}')
        create_queue('g2', None, '{"answer": 42}')

        create_queue('q1', project_id, '{"node": 31}')
        create_queue('q2', project_id, '{"node": 32}')
        create_queue('q3', project_id, '{"node": 33}')

        create_queue('q3', alt_project_id, '{"alt": 1}')

        # List (global queues)
        result = self.simulate_get(self.queue_path,
                                   query_string='limit=2&detailed=true')

        result_doc = jsonutils.loads(result[0])
        queues = result_doc['queues']
        self.assertEqual(len(queues), 2)

        for queue in queues:
            self.assertEqual(queue['metadata'], {'answer': 42})

        # List (limit)
        result = self.simulate_get(self.queue_path, headers=header,
                                   query_string='limit=2')

        result_doc = jsonutils.loads(result[0])
        self.assertEqual(len(result_doc['queues']), 2)

        # List (no metadata, get all)
        result = self.simulate_get(self.queue_path,
                                   headers=header, query_string='limit=5')

        result_doc = jsonutils.loads(result[0])
        [target, params] = result_doc['links'][0]['href'].split('?')

        self.assertEqual(self.srmock.status, falcon.HTTP_200)
        self.assertEqual(self.srmock.headers_dict['Content-Location'],
                         self.queue_path + '?limit=5')

        # Ensure we didn't pick up the queue from the alt project.
        queues = result_doc['queues']
        self.assertEqual(len(queues), 3)

        for queue in queues:
            self.simulate_get(queue['href'] + '/metadata', headers=header)
            self.assertEqual(self.srmock.status, falcon.HTTP_200)

            altheader = header.copy()
            altheader['X-Project-ID'] = 'imnothere'
            self.simulate_get(queue['href'] + '/metadata', headers=altheader)
            self.assertEqual(self.srmock.status, falcon.HTTP_404)

            self.assertNotIn('metadata', queue)

        # List with metadata
        result = self.simulate_get(self.queue_path, headers=header,
                                   query_string='detailed=true')

        self.assertEqual(self.srmock.status, falcon.HTTP_200)
        result_doc = jsonutils.loads(result[0])
        [target, params] = result_doc['links'][0]['href'].split('?')

        queue = result_doc['queues'][0]
        result = self.simulate_get(queue['href'] + '/metadata', headers=header)
        result_doc = jsonutils.loads(result[0])
        self.assertEqual(result_doc, queue['metadata'])
        self.assertEqual(result_doc, {'node': 31})

        # List tail
        self.simulate_get(target, headers=header, query_string=params)
        self.assertEqual(self.srmock.status, falcon.HTTP_204)

        # List manually-constructed tail
        self.simulate_get(target, headers=header, query_string='marker=zzz')
        self.assertEqual(self.srmock.status, falcon.HTTP_204)


class TestQueueLifecycleMongoDB(QueueLifecycleBaseTest):

    config_file = 'wsgi_mongodb.conf'

    @testing.requires_mongodb
    def setUp(self):
        super(TestQueueLifecycleMongoDB, self).setUp()

    def tearDown(self):
        storage = self.boot.storage._storage
        connection = storage.connection

        connection.drop_database(storage.queues_database)

        for db in storage.message_databases:
            connection.drop_database(db)

        super(TestQueueLifecycleMongoDB, self).tearDown()


class TestQueueLifecycleSqlalchemy(QueueLifecycleBaseTest):

    config_file = 'wsgi_sqlalchemy.conf'


class TestQueueLifecycleFaultyDriver(base.V1_1BaseFaulty):

    config_file = 'wsgi_faulty.conf'

    def test_simple(self):
        self.headers = {
            'Client-ID': str(uuid.uuid4()),
            'X-Project-ID': '338730984abc_1'
        }

        gumshoe_queue_path = self.url_prefix + '/queues/gumshoe'
        doc = '{"messages": {"ttl": 600}}'
        self.simulate_put(gumshoe_queue_path,
                          headers=self.headers,
                          body=doc)
        self.assertEqual(self.srmock.status, falcon.HTTP_503)

        location = ('Location', gumshoe_queue_path)
        self.assertNotIn(location, self.srmock.headers)

        result = self.simulate_get(gumshoe_queue_path + '/metadata',
                                   headers=self.headers)
        result_doc = jsonutils.loads(result[0])
        self.assertEqual(self.srmock.status, falcon.HTTP_503)
        self.assertNotEqual(result_doc, jsonutils.loads(doc))

        self.simulate_get(gumshoe_queue_path + '/stats',
                          headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_503)

        self.simulate_get(self.url_prefix + '/queues',
                          headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_503)

        self.simulate_delete(gumshoe_queue_path, headers=self.headers)
        self.assertEqual(self.srmock.status, falcon.HTTP_503)