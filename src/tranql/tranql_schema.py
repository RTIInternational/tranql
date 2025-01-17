import networkx as nx
import json
import yaml
import copy
import requests
import os
import time
import threading
import logging
from tranql.concept import BiolinkModelWalker
from tranql.exception import TranQLException, InvalidTransitionException
from tranql.util import snake_case, title_case
from PLATER.services.util.graph_adapter import GraphInterface
# from Levenshtein import distance as LD

logger = logging.getLogger(__name__)


class NetworkxGraph:
    def __init__(self):
        self.net = nx.MultiDiGraph ()
    def add_edge (self, start, predicate, end, properties={}):
        return self.net.add_edge (start, end, key=predicate, **properties)
    def add_node (self, identifier, label=None, properties={}):
        node = self.net.add_node (identifier, attr_dict=properties)
        return node
    def has_node (self, identifier):
        return identifier in self.net.nodes
    def get_node (self, identifier, properties=None):
        nodes = self.net.nodes(data=True)
        filtered = [i for i in nodes if i[0] == identifier]
        return filtered[0] if len(filtered) > 0 else None
    """ Returns an edge from the graph or None. Note that the edge attr dict returned is mutable.
    :param predicate: If None, will return the first edge found between start->end if one exists.
    :return: (start, predicate, end, attr_data) | None
    """
    def get_edge (self, start, end, predicate=None, properties=None):
        try:
            edges = self.net[start][end]
            if predicate is None:
                predicate = list(edges.keys())[0]
            edge_data = edges[predicate]
            return (start, predicate, end, edge_data)
        except:
            return None
    def get_nodes (self,**kwargs):
        return self.net.nodes(**kwargs)
    def get_edges (self,**kwargs):
        return self.net.edges(keys=True,**kwargs)
    def delete (self):
        self.net.clear ()
    def commit (self):
        pass

class GraphTranslator:
    """
    An interface to a knowledge graph.
      - Support a general underlying graph interface with plugins for different
        in-memory or persistent implementations.
      - Support writing and reading graphs to multiple formats.
    """
    def __init__(self, graph):
        """ Connect to an abstract graph interface. """
        self.graph = graph

    def write_kgs (self, message):
        """ Write KGS message to the graph.
        :param message: Get a knowledge graph as a network.
        :return: Return a network.
        """
        kg = message['knowledge_graph']
        nodes = kg['nodes']
        edges = kg['edges']
        for n in nodes:
            self.graph.add_node(n['id'], properties=n)
        for e in edges:
            self.graph.add_edge (e['source_id'], e['target_id'], properties=e)
        return g

    def graph_to_message(self):
        """
        Write underlying graph interface to a KGS message
        :return: Return a KGS message
        """
        nodes = [[i[0], i[1].get('attr_dict',{})] for i in list (self.graph.get_nodes (data=True))]
        edges = list (self.graph.get_edges (data=True))
        return {
            "knowledge_graph" : {
                "nodes" : nodes,
                "edges" : edges
            },
            "knowledge_map" : [
                {}
            ],
            "options" : {}
        }

class RegistryAdapter:
    def __init__(self):
        self.__registry_adapters = {
            'automat': lambda url: RegistryAdapter.__AutomatAdapter(url)  # Use this to refer things in the schema
        }

    def get_schemas(self, registry_name, registry_url, exclusion_list = []):
        """
        Adds new schemas by invoking appropriate registry
        :param registry_name:
        :param schema:
        :return:
        """
        registry_constructor = self.__registry_adapters.get(registry_name)
        if not registry_constructor:
            raise TranQLException(f'No constructor found for {registry_name} -- Error constructing schema.')
        registry = registry_constructor(registry_url)
        return registry.get_graph_schemas(exclusion_list)



    class __AutomatAdapter:
        def __init__(self, url):
            self.base_url = url.rstrip('/')

        def __get_registry(self):
            response = requests.get(self.base_url + '/registry')
            if response.status_code == 200:
                return response.json()
            else:
                raise Exception(f'Failed to contact automat registry request to server returned'
                                f'{response.status_code} -- {response.text}')

        def get_graph_schemas(self, exclusion_list=[]):
            """
            Looping through the registry we will grab the /graph/schema of each KP,
            along with it's access url.
            :return:
            """
            registry = self.__get_registry()
            main_schema = {}
            filtered_registry = filter(lambda x: x not in exclusion_list, registry)
            for path in filtered_registry:
                graph_schema_path = f'{self.base_url}/{path}/predicates'
                graph_schema = requests.get(graph_schema_path).json()
                # since we have a backplane proxy that is able to query
                # automat kps in /graph/automat/<path> we will use that pattern as url
                kp_url = f'/graph/automat/{path}'
                new_schema_name = f'automat_{path}'
                main_schema[new_schema_name] = {
                    'schema': graph_schema,
                    'url': kp_url
                }
            return main_schema


class RedisAdapter:
    registry_adapters = {}

    def __init__(self):
        pass

    @staticmethod
    def _create_graph_interface(service_name, redis_conf, tranql_config):
        redis_connection_details = redis_conf
        redis_connection_details.update(
            {
                'auth': ('', tranql_config.get(service_name.upper() + '_PASSWORD', '')),
                'db_name': 'test',
                'db_type': 'redis',
            }
        )
        return GraphInterface(
            **redis_connection_details
        )

    def _get_adapter(self, name):
        if name not in RedisAdapter.registry_adapters:
            raise ValueError(f"Redis backend with name {name} not registered.")
        return RedisAdapter.registry_adapters.get(name)

    def set_adapter(self, name, redis_config, tranql_config):
        RedisAdapter.registry_adapters[name] = RedisAdapter._create_graph_interface(
            service_name=name,
            redis_conf=redis_config,
            tranql_config=tranql_config
        )

    def get_schema(self, name):
        gi: GraphInterface = self._get_adapter(name)
        schema = gi.get_schema(force_update=True)
        return schema

    def search(self, name, query, indexes, fields=None, options={
        "prefix_search": False,
        "postprocessing_cypher": "",
        "levenshtein_distance": 0,
        "query_limit": 50,
    }):
        valid_fields = ["name", "synoynms"]
        prefix_length = 3
        # valid_fields = ["name"]
        prefix_search = options.get("prefix_search", False)
        levenshtein_distance = options.get("levenshtein_distance", 0)
        if levenshtein_distance > 0:
            options["prefix_search"] = False

        gi: GraphInterface = self._get_adapter(name)
        results = gi.search(query, indexes, fields, options)
        if prefix_search and levenshtein_distance > 0:
            search_terms = results["search_terms"]
            search_fields = valid_fields if fields is None else fields
            filtered_hits = []
            print("Searching with terms", search_terms, "and fields", search_fields)
            for hit in results["hits"]:
                b_field_terms = [hit["node"][field].split(" ") for field in search_fields if field in hit["node"]]
                matched = False
                print(search_terms, b_field_terms)
                for field_terms in b_field_terms:
                    # Every search term needs to be prefixed
                    if all([
                        # The search term only needs to be prefixed by one term in the potential completion
                        any([
                            # This is similar to how elasticsearch performs a prefix search with fuzziness.
                            # See "prefix length" in an elasticsearch fuzzy search.
                            search_term[0:prefix_length].lower() == b_term[0:prefix_length].lower()
                            # LD(search_term, b_term[0:len(search_term)]) <= levenshtein_distance
                            for b_term in field_terms
                        ])
                        for search_term in search_terms
                    ]):
                        matched = True
                        break
                if matched: filtered_hits.append(hit)
            print("Removed",[hit for hit in results["hits"] if hit not in filtered_hits])
            results["hits"] = filtered_hits
        return results["hits"]


class SchemaFactory:
    """
    Keeps a single SchemaInstance object till next update.
    """
    _cached = None
    _update_thread = None

    def __init__(self, backplane, use_registry, update_interval, tranql_config, create_new=False, skip_redis=False ):
        """
        Make a new schema object if there is nothing cached
        and start update thread.
        :param backplane:
        :param use_registry:
        """
        self.backplane = backplane
        self.use_registry = use_registry
        self.udate_interval = update_interval
        self.tranql_config = tranql_config
        self.create_new = create_new
        self.skip_redis = skip_redis

        if not SchemaFactory._cached or create_new:
            SchemaFactory._cached = Schema(backplane, use_registry, tranql_config, skip_redis=skip_redis)

        if not SchemaFactory._update_thread:
            # avoid creating multiple threads.
            SchemaFactory._update_thread = threading.Thread(
                target=SchemaFactory.update_cache_loop,
                args=(backplane, use_registry, tranql_config, skip_redis, update_interval),
                daemon=True)
            SchemaFactory._update_thread.start()

    def get_instance(self, force_update=False):
        if force_update:
            SchemaFactory._cached = Schema(self.backplane, self.use_registry, self.tranql_config, self.skip_redis)
        return copy.deepcopy(SchemaFactory._cached)

    @staticmethod
    def update_cache_loop(backplane, use_registry, tranql_config, skip_redis, update_interval=20*60):
        while True:
            SchemaFactory._cached = Schema(backplane, use_registry, tranql_config, skip_redis)
            print('sleeping..... ')
            time.sleep(update_interval)


class Schema:
    """ A schema for a distributed knowledge network. """

    def __init__(self, backplane, use_registry, tranql_config, skip_redis=False):
        """
        Create a metadata map of the knowledge network.
        """

        # String[] of errors encountered during loading.
        self.loadErrors = []
        self.registry_adapter = RegistryAdapter()

        """ Load the schema, a map of reasoner systems to maps of their schemas. """
        self.config = None
        config_file = os.environ.get("SCHEMA_CONFIG_PATH",
                                    os.path.join(os.path.dirname(__file__), "conf", "schema.yaml")
                                    )
        with open(config_file) as stream:
            self.config = yaml.safe_load(stream)

        """ Resolve remote schemas. """
        for schema_name, metadata in self.config['schema'].copy ().items ():
            if metadata.get('redis', False) and not skip_redis:
                redis_adapter = RedisAdapter()
                redis_adapter.set_adapter(schema_name, metadata.get('redis_connection_params'), tranql_config)
                metadata['schema'] = self.snake_case_schema(redis_adapter.get_schema(schema_name))
            if 'registry' in metadata:
                if use_registry:
                    registry_name = metadata['registry']
                    registry_url = metadata['registry_url']
                    exclusion_list = metadata.get('exclude', [])
                    new_schemas = self.registry_adapter.get_schemas(registry_name,
                                                                    backplane + registry_url,
                                                                    exclusion_list)
                    self.config['schema'].update(new_schemas)
                    # remove registry entry
                del self.config['schema'][schema_name]
                continue
            schema_data = metadata['schema']
            if isinstance (schema_data, str) and schema_data.startswith ("/"):
                schema_data = f"{backplane}{schema_data}"
            if isinstance(schema_data, str) and schema_data.startswith('http'):
                # If schema_data is a URL
                try:
                    old_s_d = schema_data
                    response = requests.get(schema_data)
                    schema_data = self.snake_case_schema(response.json())
                    if 'message' in schema_data:
                        raise Exception(schema_data['message'])
                except Exception as e:
                    # If the request errors for any number of reasons (likely a timeout), append an error message
                    if isinstance(e,requests.exceptions.Timeout):
                        error = 'Request timed out while fetching schema at "'+old_s_d+'"'
                    elif isinstance(e,requests.exceptions.ConnectionError):
                        error = 'Request could not connect while fetching schema at "'+old_s_d+'"'
                    else:
                        error = TranQLException('Request failed while fetching schema at "'+old_s_d+'"',details=json.dumps(next(iter(e.args)),indent=2))
                    self.loadErrors.append(error)
                    # Delete the key here because it has no data.
                    del self.config['schema'][schema_name]
                    continue
            # Else, it must already be loaded
            metadata['schema'] = schema_data
            self.config['schema'][schema_name] = metadata
        self.schema = self.config['schema']

        """ Build a graph of the schema. """
        self.schema_graph = NetworkxGraph()
        try:
            self.schema_graph.delete ()
        except:
            pass

        for k, v in self.config['schema'].items ():
            self.add_layer (layer=v['schema'], name=k)

        self.schema_graph.commit ()

    def snake_case_schema(self, schema):
        new_schema = {}
        for node in schema:
            new_node_name = snake_case(node.replace('biolink.', ''))
            sub_nodes = schema[node]
            new_schema[new_node_name] = new_schema.get(new_node_name, {})
            for sub_node in sub_nodes:
                new_subnode_name = snake_case(sub_node.replace('biolink.', ''))
                new_schema[new_node_name][new_subnode_name] = new_schema[new_node_name].get(new_subnode_name, [])
                predicates = sub_nodes[sub_node]
                for predicate in predicates:
                    new_predicate = snake_case(predicate.replace('biolink.', ''))
                    if new_predicate not in new_schema[new_node_name][new_subnode_name]:
                        new_schema[new_node_name][new_subnode_name].append(new_predicate)
        return new_schema


    def add_layer (self, layer, name=None):
        """
        :param layer: Knowledge schema metadata layers.
        """
        for source_name, targets_list in layer.items ():
            source_node = self.get_node (node_id=source_name, attrs={'reasoner': [name]})
            if name not in source_node[1]['attr_dict']['reasoner']:
                source_node[1]['attr_dict']['reasoner'].append(name)
            for target_type, links in targets_list.items ():
                target_node = self.get_node (node_id=target_type, attrs={'reasoner': [name]})
                if name not in target_node[1]['attr_dict']['reasoner']:
                    target_node[1]['attr_dict']['reasoner'].append(name)
                #self.schema_graph.commit ()
                if isinstance(links, str):
                    links = [links]
                for link in links:
                    #print (f" {source_name}->{target_type} [{link}]")
                    self.schema_graph.add_edge (source_name, link, target_type, {"reasoner":[name]})
        self.decorate_schema(layer, name)
    
    """
    Manually perform scoring on the Redis reasoner, since it supports it. If more reasoners support it,
    this should obviously be changed to be done dynamically on each reasoner and should probably be generalized
    to attribute decoration rather than just scoring.
    """
    def decorate_schema(self, layer, name=None):
        if name != "redis":
            return
        
        def toBiolink(concept):
            return "biolink:" + title_case(concept)

        redis_adapter = RedisAdapter()
        adapter = redis_adapter._get_adapter(name)
        schema_summary = adapter.summary
        for source_name, targets_list in layer.items ():
            source_node = self.get_node (node_id=source_name)
            biolink_source_name = toBiolink(source_name)
            if not biolink_source_name in schema_summary: continue
            for target_type, links in targets_list.items ():
                target_node = self.get_node (node_id=target_type)
                biolink_target_type = toBiolink(target_type)
                if not biolink_target_type in schema_summary[biolink_source_name]: continue
                edge_summary = schema_summary[biolink_source_name][biolink_target_type]
                total_count = sum(edge_summary.values())
                if isinstance(links, str):
                    links = [links]
                for link in links:
                    biolink_link = link
                    if not biolink_link in edge_summary: continue
                    (_, _, _, edge_data) = self.schema_graph.get_edge (source_name, target_type, link)
                    individual_count = edge_summary[biolink_link]
                    edge_data["score"] = individual_count / total_count
                

    def get_edge (self, plan, source_name, source_type, target_name, target_type,
                  predicate, edge_direction):
        """ Determine if a transition between two types is supported by
        any of the registered sub-schemas.
        """
        edge = None
        schema = None
        for schema_name, sub_schema_package in self.schema.items ():
            sub_schema = sub_schema_package ['schema']
            sub_schema_url = sub_schema_package ['url']
            #print (sub_schema)
            if source_type in sub_schema:
                print (f"  --{schema_name} - {source_type} => {target_type}")
                if target_type in sub_schema[source_type]:
                    top_schema = None
                    if len(plan) > 0:
                        top = plan[-1]
                        top_schema = top[0]
                    if top_schema == schema_name:
                        # this is the next edge in an ongoing segment.
                        top[2].append ([ source_name, source_type,
                                         predicate, edge_direction,
                                         target_name, target_type ])
                    else:
                        plan.append ([ schema_name, sub_schema_url, [
                            [ source_name, source_type,
                              predicate, edge_direction,
                              target_name, target_type ]
                        ]])
            else:
                implicit_conversion = BiolinkModelWalker ()
                for conv_type in implicit_conversion.get_transitions (source_type):
                    implicit_conversion_schema = "implicit_conversion"
                    implicit_conversion_url = self.schema[implicit_conversion_schema]['url']
                    if conv_type in sub_schema:
                        print (f"  --impconv: {schema_name} - {conv_type} => {target_type}")
                        if target_type in sub_schema[conv_type]:
                            plan.append ([
                                implicit_conversion_schema,
                                implicit_conversion_url,
                                [
                                    [ source_name, source_type,
                                      predicate, edge_direction,
                                      conv_type, conv_type ]
                                ]])
                            plan.append ([ schema_name, sub_schema_url, [
                                [ conv_type, conv_type,
                                  predicate, edge_direction,
                                  target_name, target_type ]
                            ]])

    def plan (self, query):
        """
        Plan a query over the configured sources and their associated schemas.
        Build a structure like this:
           source_type
              target_type
                 source
                 transition
        Start with: Linear paths; no predicates.
        """
        print (query)
        plan = []
        #plan = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for index, element_name in enumerate(query.order):
            if index == len(query.order) - 1:
                """ There's another concept to transition to. """
                continue
            self.get_edge (
                plan=plan,
                source_name = element_name,
                source_type=query.concepts[element_name].name,
                target_name = query.order[index+1],
                target_type=query.concepts[query.order[index+1]].name,
                predicate=query.arrows[index].predicate,
                edge_direction=query.arrows[index].direction)
        print (f"----------> {json.dumps(plan, indent=2)}")
        return plan

    def get_node (self, node_id, attrs={}):
        """ Get a node if it exists; create one if it doesn't.
        :param node_id: Unique id of the node.
        :return: Return the node designated by this identifier.
        """
        #return self.knet.nodes [node_id] if node_id in self.knet.nodes else \
        #    self.knet.add_node (node_id, attr_dict=attrs)
        if self.schema_graph.has_node (node_id):
            node = self.schema_graph.get_node (node_id, attrs)
            return node
        else:
            self.schema_graph.add_node (
                label="thing",
                identifier=node_id,
                properties=attrs)
            return self.schema_graph.get_node (node_id)

    def validate_edge (self, source_type, target_type):
        """
        Assert that there is an edge between these types in the schema.
        :param source_type: A source type.
        :param target_type: A target type.
        """
        source_type = snake_case(source_type.replace('biolink.', ''))
        target_type = snake_case(target_type.replace('biolink.', ''))
        edge = self.schema_graph.get_edge (start=source_type, end=target_type)
        if not edge:
            raise InvalidTransitionException (source_type, target_type, explanation=f'No valid transitions exist between {source_type} and {target_type} in this schema.')

    def validate_question (self, message):
        """
        Validate the question in a message object.
        :param message: Validate the edges in the question.
        """
        question = message['query_graph']
        nodes = question['nodes']
        for edge in question['edges']:
            edge = question['edges'][edge]
            source = nodes[edge['subject']]['category']
            target = nodes[edge['object']]['category']
            self.validate_edge (source, target)
            # print (f"  -- valid transition: {source}->{target}")


def get_test_kg (file_name):
    path = "https://raw.githubusercontent.com/NCATS-Tangerine/NCATS-ReasonerStdAPI-diff/master"
    url = f"{path}/{file_name}"
    print (url)
    return requests.get (url).json ()


def main ():
    """ Process arguments. """
    arg_parser = argparse.ArgumentParser(
        description='TranQL Schema',
        formatter_class=lambda prog: argparse.ArgumentDefaultsHelpFormatter(
            prog,
            max_help_position=180))
    arg_parser.add_argument('-s', '--create-schema', help="Create the schema.", action="store_true")
    args = arg_parser.parse_args ()
    if args.create_schema:
        print ('yeah')
