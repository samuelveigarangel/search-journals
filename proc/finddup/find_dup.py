#!/usr/bin/python
#coding: utf-8

import csv
import json
import utils
import logging
import requests
import argparse
import logging.config

from datetime import datetime, timedelta
from lxml import etree

from SolrAPI import Solr

def commit(solr, debug=False):

    if debug:
        log.info('USING DEBUG MODE. SOLR INDEX NOT UPDATED.')
    else:
        status = solr.commit()
        if status != 0:
            log.warning('Commit command at Solr index fail. Please check and execute commit at index. ')
        else:
            log.info('Commit command at Solr index successfully executed!')


def summary(total_duplicated, total_updated, total_deleted, fail_list, debug=False):

    if fail_list:
        log.warning('Unable to update the following articles {0}'.format(fail_list))

    if not debug:
        log.info('Update index complete! Duplications ({0}), Updated ({1}), Deleted ({2}), ({3}) fails.'.format(
            int(total_duplicated), total_updated, total_deleted, len(fail_list)))

    log.info('End of find duplication script.')


def get_duplication_list(solr, offset):
    limit_to_process = settings['params']['limit_offset']

    dup_params = {'q' : '*:*', 'fl' : 'id', 'f.dedup_signature.facet.offset' : offset,
        'f.dedup_signature.facet.limit' : limit_to_process, 'json.nl' : 'arrarr' }

    log.debug('Get duplication list (dedup_signature facet) offset: {0}'.format(offset))

    response = solr.select(dup_params)
    response_json = json.loads(response)
    
    return response_json['facet_counts']['facet_fields']['dedup_signature']


def get_duplication_articles(solr, dup_code):

    dedup_query = 'dedup_signature:"{0}"'.format(dup_code)

    dup_params = {'q' : dedup_query, 'fl' : '*'}

    log.debug('Get articles with dedup_signature: {0}'.format(dup_code))

    response = solr.select(dup_params)
    response_json = json.loads(response)
    
    return response_json['response']['docs']

def save_csv_entry(csv, article):
    title = article['ti'][0].encode('utf-8')
    source = article['fo'][0].encode('utf-8')
    authors = ', '.join(article['au']).encode('utf-8')
    colection = article['in']

    csv.writerow( [article['id'], title, authors, source, colection] )


def update_solr_document(solr, id, field_name, field_value):

    add = etree.Element('add')
    doc = etree.Element('doc')

    field_id = etree.Element('field', name='id')
    field_id.text = id
    doc.append(field_id)

    if isinstance(field_value, list):
        for occ in field_value:
            field = etree.Element('field', name=field_name, update='set')
            field.text = occ
            doc.append(field)
    else:
        field = etree.Element('field', name=field_name, update='set')
        field.text = field_value
        doc.append(field)

    add.append(doc)
    update_xml = etree.tostring(add, pretty_print=True)

    status = solr.update(update_xml)

    return status


def main(settings, *args, **xargs):

    solr = Solr(settings['endpoints']['solr'], timeout=int(settings['request']['timeout']))

    parser = argparse.ArgumentParser(description='Script to handle article duplication on article index')

    parser.add_argument('-d', '--debug',
                        action='store_true',
                        help='execute the script in DEBUG mode (don\'t update the index)')

    parser.add_argument('-v', '--version',
                        action='version',
                        version='%(prog)s 0.1')

    args = parser.parse_args()

    if args.debug:
        log.setLevel(logging.DEBUG)

    log.info('Start find duplication script')

    # set csv file for register duplication articles
    csv_filename = '{0}-{1}.csv'.format(settings['csv']['filename_prefix'],
         datetime.now().strftime('%Y-%m-%d') )
    csv_file = open(csv_filename, 'wb')
    csv_writer = csv.writer(csv_file, quoting=csv.QUOTE_MINIMAL)

    total_duplicated = 0
    total_deleted = 0
    total_updated = 0
    offset = 0
    fail_list = []

    while True:
        try:
            duplication_lst = get_duplication_list(solr, offset)
            total_for_process = len(duplication_lst)

            if total_for_process == 0:
                break;

            log.info('Processing {0} duplication entries'.format(total_for_process))

            offset += int(settings['params']['limit_offset'])

            for dup_code in duplication_lst:
                article_lst = get_duplication_articles(solr, dup_code[0])
                article_id_list = []

                for article in article_lst:
                    article_id = article['id']
                    article_id_list.append( {'id': article_id, 'col': article['in']} )
                    # add CSV row for duplicated article
                    save_csv_entry(csv_writer, article)

                if article_id_list:
                    main_article = [ article['id'] for article in article_id_list if '-scl' in article['id'] ]

                    if main_article:
                        for update_article in article_id_list:
                            update_id = update_article['id']
                            # if is the main article (SCL colection) update index
                            # otherwise delete article duplication
                            if update_id == main_article[0]:
                                log.info('Updating colection element of article: {0}'.format(update_id))
                                colection_list = [art['col'] for art in article_id_list]

                                if not args.debug:
                                    status = update_solr_document(solr, update_id, 'in', 
                                            colection_list)
                                    if status == 0:
                                        total_updated += 1

                            else:
                                log.info('Deleting duplicated article: {0}'.format(update_id))
                                total_duplicated += 1

                                if not args.debug:
                                    delete_query = 'id:"{0}"'.format(update_id)
                                    status = solr.delete(delete_query)
                                    if status == 0:
                                        total_deleted += 1

                            # check for udpate solr status (update or delete)
                            if not args.debug and status != 0:
                                log.error('Unable to update article {0}, code:{1}'.format(
                                        update_id, status))
                                fail_list.append(update_id)

                            
                    else:
                        log.warning('Ignoring article id\'s for missing main article of SCL collection :{0}'.format(
                            [art['id'] for art in article_id_list]) )

                # write a empty line for separate next group of duplication articles
                csv_writer.writerow([' '])

            #commit on any offset cycle
            commit(solr, debug=args.debug)
        except Exception as e:
            log.critical('Unexpected error: {0}'.format(e))

    summary(total_duplicated, total_updated, total_deleted, fail_list, args.debug)


if __name__ == "__main__":

    # config app file
    config = utils.Configuration.from_env()
    settings = dict(config.items())

    # config logger file
    logging.config.fileConfig('logging.conf')

    # create logger
    log = logging.getLogger('find_dup')

    # execute update solr script
    main(settings)
