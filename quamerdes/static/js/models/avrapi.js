define([
    'jquery',
    'underscore',
    'backbone',
    'app'
],
function($, _, Backbone, app){
    AvrApiModel = Backbone.Model.extend({
        defaults: function(){
            return {
                enabledCollections: AVAILABLE_INDICES,
                enabledAggregations: AVAILABLE_AGGREGATIONS,
                // enabledSearchHitFields: SEARCH_HIT_FIELDS,
                hitsPerPage: HITS_PER_PAGE,
                startAtHit: 0,
                currentPage: 1,
                highlightFragments: HIT_HIGHLIGHT_FRAGMENTS,
                highlightFragmentSize: HIT_HIGHLIGHT_FRAGMENT_SIZE,
                highlightFields: HIT_HIGHLIGHT_FIELDS,
                queryString: null,
                filters: {},
                currentPayload: { facets: {}},

                hits: {},
                totalHits: 0,
                queryTime: 0,
                queryTimeMs: null,

                // Total number of documents in the index
                totalDocs: 0,
                // First broadcast (broadcastDates.start) in the index
                firstBroadcastDate: null,
                // Most recent broadcast (broadcastDates.start) in the index
                lastBroadcastDate: null,
                // Number of broadcasts that contain one or more tweets
                docsWithTweetsCount: null,
                // Number of broadcasts that contain subtitles
                docsWithSubtitleCount: null,

                defaultInterval: AVAILABLE_AGGREGATIONS.dates.date_histogram.interval,
                interval: null,

                user: USER
            };
        },

        initialize: function(){
            this.api_url = 'api/';

            var self = this;
            app.vent.on('QueryInput:input:' + this.get('name'), function(){
                self.set('minDate', Infinity);
                self.set('maxDate', -Infinity);
            });

            app.vent.on('interval:set', function(){
                self.setHistogram();
            });

            app.vent.on('QueryInput:input:' + this.get('name'), function(){
                self.set('interval', null);
            });
        },

        http: {
            get: function(url, data, callback){
                url = ['api', url].join('/');

                //if (DEBUG) console.log('AvrApiModel:http:post', url, payload);
                $.ajax({
                    url: url,
                    type: 'GET',
                    data: data,
                    dataType: 'json',
                    cache: true,
                    success: callback,
                    error: function(xhr, status, error){
                        console.log(xhr);
                        console.log(status);
                        console.log(error);
                    }
                });
            },
            post: function(url, data, callback){
                if($.inArray(url, ['search', 'count']) !== -1){
                    data = {'payload': JSON.stringify(data)};
                }
                else if(url === 'log_usage'){
                    data = {'events': JSON.stringify(data)};
                }

                url = ['api', url].join('/');

                //if (DEBUG) console.log('AvrApiModel:http:post', url, payload);
                $.ajax({
                    url: url,
                    type: 'POST',
                    data: data,
                    dataType: 'json',
                    cache: true,
                    success: callback,
                    error: function(xhr, status, error){
                        console.log(xhr);
                        console.log(status);
                        console.log(error);
                    }
                });
            }
        },

        register: function(email, name, organization, password){
            var post_data = {
                email: email,
                name: name,
                organization: organization,
                password: password
            };

            var self = this;

            // On successful login, set the user details and trigger 'login_successful' event
            this.http.post('register', post_data, function(data){
                if(data.success){
                    app.vent.trigger('AvrApiModel:registration_successful');
                }
                // Trigger 'login_failed' with the error
                else {
                    app.vent.trigger('AvrApiModel:registration_failed', data.errors);
                }
            });
        },

        login: function(email, password){
            var post_data = {
                email: email,
                password: password
            };

            var self = this;
            this.http.post('login', post_data, function(data){
                // On successful login, set the user details and trigger 'login_successful' event
                if(data.success){
                    self.set('user', data.user);
                    app.vent.trigger('AvrApiModel:login_successful');
                }
                // Trigger 'login_failed' with the error
                else {
                    app.vent.trigger('AvrApiModel:login_failed', data.errors);
                }
            });
        },

        logout: function(){
            var self = this;
            this.set('user', null);
            this.http.get('logout', function(data){
                console.log(data);
            });
        },

        logUsage: function(events){
            this.http.post('log_usage', events, function(data){
                console.log(data);
            });
        },

        // Use the query properties that are set as instance attributes to generate
        // an ES query
        constructQueryPayload: function(){
            var self = this;
            var payload = {
                query: {},
                index: 'quamerdes_immix'
            };

            // Determine which index to search on
            // _.each(this.get('enabledCollections'), function(source){
            //     // Determine which indices to search. Defaults to all
            //     payload.indices.push('quamerdes_' + source.id);
            // });

            payload.query.filtered = {
                query: {
                    query_string: {
                        query: this.get('queryString'),
                        default_operator: 'and'
                    }
                },
                filter: {
                    bool: {must: []}
                }
            }

            /*
            FILTERING
            */
            var filters = this.get('filters');
            _.each(filters, function(filter, filtername){
                // Generic filter structure
                filter.type = filter.type == 'terms' ? 'term' : filter.type;
                var f = {};
                f[filter.type] = {};

                // Process range filter values
                if(filter.type == 'range'){
                    f[filter.type][filter.field] = {}
                    f[filter.type][filter.field]['from'] = filter.values.from;
                    f[filter.type][filter.field]['to'] = filter.values.to;
                }
                //
                else if(filter.type == 'term'){
                    f[filter.type][filter.field] = {}
                    f[filter.type][filter.field] = filter.values
                }
                else if(filter.type == 'nested'){
                    f[filter.type] = {
                        path: filter.values.path,
                        filter: {term: {}}
                    };
                    f[filter.type].filter.term[filter.values.field] = filter.values.value;
                }

                payload.query.filtered.filter.bool.must.push(f);
            });

            /*
            SNIPPETS AND HIGHLIGHTING
            */
            var highlight = {
                fields: {},
                number_of_fragments: this.get('highlightFragments'),
                fragment_size: this.get('highlightFragmentSize'),
                order: 'score'
            };

            _.each(this.get('highlightFields'), function(field){
                highlight.fields[field] = {};
            });
            payload.highlight = highlight;

            /*
            AGGREGATIONS
            */
            var aggregations = {},
                ESAggFields = ['date_histogram', 'terms', 'nested', 'significant_terms'];

            _.each(this.get('enabledAggregations'), function(aggregation, aggregationName){
                aggregations[aggregationName] = {};
                _.each(aggregation, function(optionValue, optionName){
                    if(_.contains(ESAggFields, optionName)){
                        if(optionName == 'date_histogram'){
                            var interval = self.get('interval');
                            if(!interval) interval = self.get('defaultInterval');
                            optionValue.interval = interval;

                            aggregations[aggregationName][optionName] = optionValue;
                        }
                        else if(optionName == 'nested'){
                            aggregations[aggregationName] = optionValue;
                        }
                        else {
                            aggregations[aggregationName][optionName] = optionValue;
                        }
                    }
                });
            });

            payload.aggregations = aggregations;

            console.log('===============\n', payload, '\n===============');
            return payload;

            // // Only add the filter component to the query if at least one filter
            // // is enabled.
            // if(_.size(filters) > 0){
            //     // Each facet is added as an AND filter
            //     filtered.filter = {and: []};
            // }

            // _.each(filters, function(filter, facet_name){
            //     // Term based filtering on nested attributes
            //     if(filter.nested && filter.facet_type === 'terms'){
            //         _.each(filter.values, function(value){
            //             var facet = {};
            //             facet.nested = {
            //                 path: filter.path,
            //                 // Take all nested documents of 'path' into consideration
            //                 query: {match_all: {}}
            //             };

            //             // facet_filters and facet values are added as AND conditions
            //             facet.nested.filter = { and: []};

            //             // Use the specified field when dealing with multi-field types
            //             if('nested_filter_field' in filter){
            //                 field = filter.path + '.' + filter.nested_filter_field + '.' + filter.field;
            //             }
            //             else {
            //                 field = filter.path + '.' + filter.field;
            //             }

            //             var term_filter = {term : {}};
            //             term_filter.term[field] = value;
            //             facet.nested.filter.and.push(term_filter);

            //             // Add additonal filters for nested docuent selection
            //             if('facet_filter' in filter){
            //                 facet.nested.filter.and.push(filter.facet_filter);
            //             }

            //             filtered.filter.and.push(facet);
            //         });
            //     }

            //     // Date range filter on nested attributes
            //     else if(filter.nested && filter.facet_type === 'range'){
            //         var facet = {};
            //         facet.nested = {
            //             path: filter.path,
            //             // Take all nested documents of 'path' into consideration
            //             query: {match_all: {}}
            //         };

            //         // facet_filters and facet values are added as AND conditions
            //         facet.nested.filter = { and: []};

            //         // Use the specified field when dealing with multi-field types
            //         if('nested_filter_field' in filter){
            //             field = filter.path + '.' + filter.nested_filter_field + '.' + filter.field;
            //         }
            //         else {
            //             field = filter.path + '.' + filter.field;
            //         }

            //         var range_filter = {};
            //         range_filter[field] = {
            //             'from': filter.values.from,
            //             'to': filter.values.to
            //         };

            //         facet.nested.filter.and.push({'range': range_filter});

            //         if('facet_filter' in filter){
            //             facet.nested.filter.and.push(filter.facet_filter);
            //         }

            //         filtered.filter.and.push(facet);
            //     }

            //     // Term based filtering on non-nested attributes
            //     else if(filter.facet_type === 'terms'){
            //         var facet = {};
            //         field = filter.field;
            //         values = filter.values;
            //         facet.terms = {};
            //         facet.terms[field] = values;
            //         facet.terms.execution = 'and';

            //         filtered.filter.and.push(facet);
            //     }
            // });

            // // Add the filters to the query payload
            // payload.query.filtered = filtered;

            // // The fields that are required to render the search results templates
            // payload._source = this.get('enabledSearchHitFields');

            // // Number of hits to return and the offset
            // payload.size = this.get('hitsPerPage');
            // payload.from = this.get('startAtHit');
            // console.log('============\n', payload)
            // return payload;
        },

        changeSearchFields: function(enabled_fields){
            if (DEBUG) console.log('ElasticSearchModel:changeSearchFields', enabled_fields);

            var self = this;

            // Get the config definitions of the enabled fields
            var field_definitions = _.filter(AVAILABLE_INDICES, function(field){
                if(_.contains(enabled_fields, field.id)){
                    return true;
                } else {
                    return false;
                }
            });

            this.set('enabledCollections', field_definitions);
            this.set('currentPayload', this.constructQueryPayload());

            this.http.post('search', this.get('currentPayload'), function(data){
                self.set({
                    hits: data.hits.hits,
                    aggregations: data.aggregations,
                    totalHits: data.hits.total,
                    queryTime: data.took,
                    queryTimeMs: (data.took / 1000).toFixed(2)
                });
            });
        },

        // Execute a new query based on an ft query string and the default
        // query properties defined in the config
        freeTextQuery: function(querystring){
            var self = this;

            // Reset query properties
            this.set({
                enabledAggregations: AVAILABLE_AGGREGATIONS,
                hitsPerPage: HITS_PER_PAGE,
                startAtHit: 0,
                currentPage: 1,
                queryString: querystring,

                // TODO: use other filter here
                // This filter was introduced by B&G to ensure returned objects
                // had a date, and should cover any data they have; however, the
                // KB corpus goes back further, so we may want to do another
                // filter here
                filters: {
                    dates: {
                        type: "range",
                        field: "date",
                        values: {
                            from: new Date(1951, 10, 2),
                            to: new Date()
                        }
                    }
                }
            });

            this.set('currentPayload', this.constructQueryPayload());
            this.http.post('search', this.get('currentPayload'), function(data){
                self.set({
                    hits: data.hits.hits,
                    aggregations: data.aggregations,
                    totalHits: data.hits.total,
                    queryTime: data.took,
                    queryTimeMs: (data.took / 1000).toFixed(2),
                    queryString: querystring
                });
            });
        },

        // Add or remove facet values from the set of active filters
        modifyAggregationFilter: function(aggregation, value, add){
            console.log('AvrApiModel:modifyAggregationFilter', aggregation, value);
            var self = this;
            var aggregation_settings = AVAILABLE_AGGREGATIONS[aggregation];

            // Get the currently active filters
            var filters = this.get('filters');

            // Add filter definitions to the filters object if it does not yet exist
            if(!(aggregation in filters)){
                // Facet of the 'terms' type
                if('terms' in aggregation_settings){
                    filters[aggregation] = {
                        type: 'term',
                        field: aggregation_settings.terms.field,
                        values: ''
                    };
                }
                else if('date_histogram' in aggregation_settings){
                    filters[aggregation] = {
                        type: 'range',
                        field: aggregation_settings.date_histogram.field,
                        values: {}
                    };
                }
                else if('nested' in aggregation_settings){
                    filters[aggregation] = {
                        type: 'nested',
                        values: {}
                    }
                }
            }

            // Add or delete a facet value from the filters values array
            if('terms' in aggregation_settings){
                // add term to terms filter
                if(add){
                    filters[aggregation].values = value;
                }
                else {
                    delete filters[aggregation];
                }
            }
            else if('date_histogram' in aggregation_settings){
                if(add){
                    filters[aggregation].values.from = value[0];
                    filters[aggregation].values.to = value[1];
                }
                else {
                    delete filters[aggregation];
                }
            }
            else if('nested' in aggregation_settings){
                if(add){
                    filters[aggregation].values.path = aggregation_settings.nested_filter.path;
                    filters[aggregation].values.field = aggregation_settings.nested_filter.field;
                    filters[aggregation].values.value = value;
                }
                else {
                    delete filters[aggregation];
                }
            }

            this.set('filters', filters);
            this.set('currentPayload', this.constructQueryPayload());
            this.http.post('search', this.get('currentPayload'), function(data){
                self.set({
                    hits: data.hits.hits,
                    aggregations: data.aggregations,
                    queryTime: data.took,
                    totalHits: data.hits.total,
                    queryTimeMs: (data.took / 1000).toFixed(2)
                });
            });
        },

        getDateHistogram: function(options, callback){
            var self = this;
            var interval = this.get('interval');
            if (!interval){
                interval = this.get('defaultInterval');
            }
            if(options.interval){
                interval = options.interval;
            }

            var payload = _.clone(this.get('currentPayload'));
            delete payload.highlight;
            payload.size = 0;
            payload.aggregations = {
                dates: {
                    date_histogram: {
                        field: 'date',
                        interval: interval
                    }
                }
            }

            this.http.post('search', payload, function(data){
                callback(data);
            });
        },

        setHistogram: function(){
            if(!this.get('interval')){
                // No need to set a histogram
                return;
            }
            var self = this;
            var payload = _.clone(this.get('currentPayload'));

            delete payload.highlight;
            payload.size = 0;
            payload.aggregations = {
                dates: {
                    date_histogram: {
                        field: 'date',
                        interval: this.get('interval')
                    }
                }
            };

            this.http.post('search', payload, function(data){
                var aggregations = self.get('aggregations');
                aggregations.dates = data.aggregations.dates;

                self.set({
                    aggregations: aggregations
                }, {silent: true});

                app.vent.trigger('model:redraw:' + self.get('name'));
            });
        },

        // Navigate to a given page using the existing query (currentPayload)
        paginateToPage: function(page){
            var self = this;
            this.set('startAtHit', this.get('hitsPerPage') * (page - 1));

            // Get a copy of the current payload
            var payload = this.get('currentPayload');
            payload.from = this.get('startAtHit');

            // Since we only have to replace hits, don't request facets. This is less
            // expensive on the ES side, and reduces the size of the response.
            delete payload.facets;

            this.http.post('search', payload, function(data){
                self.set({
                    hits: data.hits.hits,
                    queryTime: data.took,
                    queryTimeMs: (data.took / 1000).toFixed(2)
                });
            });
        },

        /* Get the total number of documents that are currently in the index */
        getTotalDocCount: function(){
            var self = this;
            this.http.post('count', {'query': {'match_all': {}}}, function(data){
                self.set('totalDocs', data.count);
            });
        },

        /* Get the date of the first and last broadcasts in the index */
        getFirstLastDocDates: function(){
            var self = this;

            var query = {
                "query": {"match_all": {}},
                "facets": {
                    "min_max_broadcast_start_date": {
                        "nested": "broadcastDates",
                        "statistical": {
                            "field": "start"
                        },
                        "facet_filter": {
                            "range": {
                                "broadcastDates.start": {
                                    "gte": new Date(1800,1,1),
                                    "lte": new Date()
                                }
                            }
                        }
                    }
                },
                "size": 0
            };
            this.http.post('search', query, function(data){
                self.set({
                    firstBroadcastDate: new Date(data.facets.min_max_broadcast_start_date.min),
                    lastBroadcastDate: new Date(data.facets.min_max_broadcast_start_date.max)
                });
            });
        },

        /* Get the number of documents that contain one or more Tweet */
        getDocsWithTweetsCount: function(){
            var self = this;
            var query = {
              "query": {
                "filtered": {
                  "query": { "match_all": {} },
                  "filter": {
                    "nested": {
                      "path": "tweets",
                      "query": { "match_all": {} },
                      "filter": {
                        "not": {
                          "missing": {
                            "field": "tweetId",
                            "existence": true
                          }
                        }
                      }
                    }
                  }
                }
              },
              "size": 0
            };

            this.http.post('search', query, function(data){
                self.set('docsWithTweetsCount', data.hits.total);
            });
        },

        getDocsWithSubtitleCount: function(){
            var self = this;
            var query = {
                "query": {
                    "filtered": {
                        "query": {
                            "match_all": {}
                        },
                        "filter": {
                            "exists": {
                                "field": "subtitles"
                            }
                        }
                    }
                },
                "size": 0
            };

            this.http.post('search', query, function(data){
                self.set('docsWithSubtitleCount', data.hits.total);
            });
        }
    });

    return AvrApiModel;
});
