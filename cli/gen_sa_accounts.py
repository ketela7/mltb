import errno
import os
import pickle
from argparse import ArgumentParser
from base64 import b64decode
from glob import glob
from json import loads
from random import choice
from time import sleep

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/cloud-platform',
          'https://www.googleapis.com/auth/iam']
project_create_ops = []
current_key_dump = []
sleep_time = 60


def parse_error(e):
    e = str(e)
    status_code = e.split("<HttpError ")[1].split(" ")[0]
    jason = e.split('Details: "')[1].split('">')[0].replace("'", "\"")
    return int(status_code), loads(jason)


# Generate a random id
def _generate_id(prefix='sa-'):
    chars = '-abcdefghijklmnopqrstuvwxyz1234567890'
    return prefix + ''.join(choice(chars) for _ in range(25)) + choice(chars[1:])


# List projects using service
def _get_projects(service):
    return [i['projectId'] for i in service.projects().list().execute()['projects']]


# Default batch callback handler
def _def_batch_resp(id, resp, exception):
    if exception:
        status_code, jason = parse_error(exception)

        if status_code == 429:
            sleep_time = int(jason[0]['retryDelay'].split('s')[0])
            print("sleeping... ", sleep_time)
            sleep(sleep_time)
        else:
            resource_id = jason[1]['metadata']['resource_id']
            reason = jason[1]['reason']
            raise Exception(
                f"Project: {resource_id} {status_code} ERROR: {reason}")


# Project Creation Batch Handler
def _pc_resp(id, resp, exception):
    global project_create_ops
    if exception:
        status_code, jason = parse_error(exception)
        resource_id = jason[1]['metadata']['resource_id']
        reason = jason[1]['reason']
        print(f"Project: {resource_id} {status_code} ERROR: {reason}")

    else:
        for i in resp.values():
            project_create_ops.append(i)


# Create count SAs in project
def _create_accounts(service, project):
    batch = service.new_batch_http_request(callback=_def_batch_resp)
    aid = _generate_id('aid-')
    batch.add(service.projects().serviceAccounts().create(name='projects/' + project, body={'accountId': aid,
                                                                                            'serviceAccount': {
                                                                                                'displayName': aid}}))
    batch.execute()


# Create accounts needed to fill project
def _create_remaining_accounts(iam, project):
    while (sa_count := len(_list_sas(iam, project))) < 100:
        print('Creating accounts in', project, f"({sa_count})")
        try:
            _create_accounts(iam, project)
        except Exception as e:
            print(e)
            break


# Project Creation
def _create_projects(cloud, count):
    global project_create_ops
    batch = cloud.new_batch_http_request(callback=_pc_resp)
    new_projs = []
    for _ in range(count):
        new_proj = _generate_id()
        new_projs.append(new_proj)
        batch.add(cloud.projects().create(body={'project_id': new_proj}))
    batch.execute()

    for i in project_create_ops:
        while True:
            resp = cloud.operations().get(name=i).execute()
            if resp.get("done"):
                break
            sleep(3)
    return new_projs


# Enable services ste for projects in projects
def _enable_services(service, projects, ste):
    batch = service.new_batch_http_request(callback=_def_batch_resp)
    for i in projects:
        for j in ste:
            batch.add(service.services().enable(
                name='projects/%s/services/%s' % (i, j)))
    try:
        batch.execute()
    except Exception:
        status_code, jason = parse_error(exception)
        resource_id = jason[1]['metadata']['resource_id']
        reason = jason[1]['reason']
        print(f"Project: {resource_id} {status_code} ERROR: {reason}")
        projects.remove(resource_id)


# List SAs in project
def _list_sas(iam, project):
    try:
        resp = iam.projects().serviceAccounts().list(
            name='projects/' + project, pageSize=100).execute()
        if 'accounts' in resp:
            return resp['accounts']
    except:
        pass

    return []


# Create Keys Batch Handler
def _batch_keys_resp(id, resp, exception):
    global current_key_dump
    if not exception:
        current_key_dump.extend([(
            resp['name'][resp['name'].rfind('/'):],
            b64decode(resp['privateKeyData']).decode('utf-8')
        )])


# Create Keys
def _create_sa_keys(iam, projects, path):
    global current_key_dump
    for i in projects:
        print('Downloading keys from %s' % i)
        current_key_dump = []
        batch = iam.new_batch_http_request(callback=_batch_keys_resp)
        total_sas = _list_sas(iam, i)
        print(len(total_sas))
        for j in total_sas:
            batch.add(iam.projects().serviceAccounts().keys().create(
                name='projects/%s/serviceAccounts/%s' % (i, j['uniqueId']),
                body={
                    'privateKeyType': 'TYPE_GOOGLE_CREDENTIALS_FILE',
                    'keyAlgorithm': 'KEY_ALG_RSA_2048'
                }
            ))
        batch.execute()

        dir_path = f'{path}/{i}/accounts' if path != "accounts" else path
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        print("saving to:", dir_path)
        for index, j in enumerate(current_key_dump, start=0):
            with open(f"{dir_path}/{index}.json", 'w+') as f:
                f.write(j[1])


# Delete Service Accounts
def _delete_sas(iam, project):
    sas = _list_sas(iam, project)
    batch = iam.new_batch_http_request(callback=_def_batch_resp)
    for i in sas:
        batch.add(iam.projects().serviceAccounts().delete(name=i['name']))
    batch.execute()


def serviceaccountfactory(
        credentials='credentials.json',
        token='token_sa.pickle',
        path=None,
        list_projects=False,
        list_sas=None,
        create_projects=None,
        max_projects=12,
        enable_services=None,
        services=['iam', 'drive'],
        create_sas=None,
        delete_sas=None,
        download_keys=None,
        parse_mail=False
):

    selected_projects = []
    proj_id = loads(open(credentials, 'r').read())['installed']['project_id']
    creds = None
    if os.path.exists(token):
        with open(token, 'rb') as t:
            creds = pickle.load(t)

    req_browser = False
    if not creds or not creds.valid or creds.expired and creds.refresh_token:
        try:
            req_browser = False
            creds.refresh(Request())
        except RefreshError:
            req_browser = True

    if req_browser:
        flow = InstalledAppFlow.from_client_secrets_file(
            credentials, SCOPES)

        creds = flow.run_local_server(port=0, open_browser=False)

        with open(token, 'wb') as t:
            pickle.dump(creds, t)

    cloud = build('cloudresourcemanager', 'v1', credentials=creds)
    iam = build('iam', 'v1', credentials=creds)
    serviceusage = build('serviceusage', 'v1', credentials=creds)

    projs = None
    while not projs:
        try:
            projs = _get_projects(cloud)
        except HttpError as e:
            if loads(e.content.decode('utf-8'))['error']['status'] == 'PERMISSION_DENIED':
                try:
                    serviceusage.services().enable(
                        name='projects/%s/services/cloudresourcemanager.googleapis.com' % proj_id).execute()
                except HttpError as e:
                    print(e._get_reason())
                    input('Press Enter to retry.')

    if list_projects:
        return _get_projects(cloud)

    if list_sas:
        return _list_sas(iam, list_sas)

    if create_projects:
        print("creat projects request is: {}".format(create_projects))
        if create_projects > 0:
            current_count = len(_get_projects(cloud))
            if current_count + create_projects <= max_projects:
                print('Creating %d projects' % (create_projects))
                selected_projects = _create_projects(cloud, create_projects)
            else:
                free_total = max_projects - current_count
                print("max:", max_projects)
                print("current:", current_count)
                print("free:", free_total)
                print("\n Just can create project in total", free_total)
                input("Press Enter to continue...")
                selected_projects = _create_projects(cloud, free_total)
        else:
            print('max SAcc is 100, this process will be continue generate SAcc until 100 acc per project. \n'
                  'So make sure you have some projects already.')
            input("Press Enter to continue...")

    if enable_services:
        ste = [enable_services]
        if enable_services == '~':
            ste = selected_projects
        elif enable_services == '*':
            ste = _get_projects(cloud)
        services = [i + '.googleapis.com' for i in services]
        print('Enabling services')
        _enable_services(serviceusage, ste, services)

    if create_sas:
        stc = [create_sas]
        if create_sas == '~':
            stc = selected_projects
        elif create_sas == '*':
            stc = _get_projects(cloud)
        for i in stc:
            _create_remaining_accounts(iam, i)

    if download_keys:
        try:
            os.mkdir(path)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
        pjs = [download_keys]
        if download_keys == '~':
            pjs = selected_projects
        elif download_keys == '*':
            pjs = _get_projects(cloud)
        _create_sa_keys(iam, pjs, path)

    if delete_sas:
        std = []
        std.append(delete_sas)
        if delete_sas == '~':
            std = selected_projects
        elif delete_sas == '*':
            std = _get_projects(cloud)
        for i in std:
            print('Deleting service accounts in %s' % i)
            _delete_sas(iam, i)

    if parse_mail and path:
        for p in os.listdir(path):
            dir_path = os.path.join(path, p)
            if os.path.isdir(dir_path):
                out_mail = f"{dir_path}/mail.txt" if path != "accounts" else "mail.txt"
                reg = "'\"client_email\": \"\K[^\"]+'"
                os.system(
                    f"grep -oPh {reg} {dir_path}/accounts/*.json > {out_mail}")


if __name__ == '__main__':
    parse = ArgumentParser(
        description='A tool to create Google service accounts.')
    parse.add_argument('--path', '-p', default='accounts',
                       help='Specify an alternate directory to output the credential files.')
    parse.add_argument('--token', default='token_sa.pickle',
                       help='Specify the pickle token file path.')
    parse.add_argument('--credentials', default='credentials.json',
                       help='Specify the credentials file path.')
    parse.add_argument('--list-projects', default=False, action='store_true',
                       help='List projects viewable by the user.')
    parse.add_argument('--list-sas', default=False,
                       help='List service accounts in a project.')
    parse.add_argument('--create-projects', type=int,
                       default=None, help='Creates up to N projects.')
    parse.add_argument('--max-projects', type=int, default=12,
                       help='Max amount of project allowed. Default: 12')
    parse.add_argument('--enable-services', default=None,
                       help='Enables services on the project. Default: IAM and Drive')
    parse.add_argument('--services', nargs='+', default=['iam', 'drive'],
                       help='Specify a different set of services to enable. Overrides the default.')
    parse.add_argument('--create-sas', default=None,
                       help='Create service accounts in a project.')
    parse.add_argument('--delete-sas', default=None,
                       help='Delete service accounts in a project.')
    parse.add_argument('--download-keys', default=None,
                       help='Download keys for all the service accounts in a project.')
    parse.add_argument('--quick-setup', default=None, type=int,
                       help='Create projects, enable services, create service accounts and download keys. ')
    parse.add_argument('--new-only', default=False,
                       action='store_true', help='Do not use exisiting projects.')
    parse.add_argument('--parse-mail', default=False,
                       action='store_true', help='parse sa.json to mail list')
    args = parse.parse_args()

    if path := args.path:
        args.token = f"{path}/token_sa.pickle"
        args.credentials = f"{path}/credentials.json"

    # If credentials file is invalid, search for one.
    if not os.path.exists(args.credentials):
        options = glob('*.json')
        print('No credentials found at %s. Please enable the Drive API in:\n'
              'https://developers.google.com/drive/api/v3/quickstart/python\n'
              'and save the json file as credentials.json' % args.credentials)
        if len(options) < 1:
            exit(-1)
        else:
            print('Select a credentials file below.')
            inp_options = [str(i) for i in list(
                range(1, len(options) + 1))] + options
            for i in range(len(options)):
                print('  %d) %s' % (i + 1, options[i]))
            inp = None
            while True:
                inp = input('> ')
                if inp in inp_options:
                    break
            args.credentials = inp if inp in options else options[int(inp) - 1]
            print('Use --credentials %s next time to use this credentials file.' %
                  args.credentials)
    if args.quick_setup:
        opt = '~' if args.new_only else '*'
        args.services = ['iam', 'drive']
        args.create_projects = args.quick_setup
        args.enable_services = opt
        args.create_sas = opt
        args.download_keys = opt
    resp = serviceaccountfactory(
        path=args.path,
        token=args.token,
        credentials=args.credentials,
        list_projects=args.list_projects,
        list_sas=args.list_sas,
        create_projects=args.create_projects,
        max_projects=args.max_projects,
        create_sas=args.create_sas,
        delete_sas=args.delete_sas,
        enable_services=args.enable_services,
        services=args.services,
        download_keys=args.download_keys,
        parse_mail=args.parse_mail
    )
    if resp is not None:
        if args.list_projects:
            if resp:
                print('Projects (%d):' % len(resp))
                for i in resp:
                    print('  ' + i)
            else:
                print('No projects.')
        elif args.list_sas:
            if resp:
                print('[+] Service accounts in %s (%d):' %
                      (args.list_sas, len(resp)))
                for i in resp:
                    # print('  %s (%s)' % (i['email'], i['uniqueId']))
                    print(i['email'])
            else:
                print('No service accounts.')
