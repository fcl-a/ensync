import os, sys
import codecs
import base64
import re
import locale

import logging
import traceback

import xmltodict
import json

from time import gmtime,strftime
import lxml.etree as ET
from lxml.etree import SubElement
from xml.sax.saxutils import escape, unescape

import evernote.edam.type.ttypes as Types
from evernote.edam.limits.constants import EDAM_USER_NOTES_MAX

import geeknote.config
geeknote.config.APP_DIR = os.path.abspath('.') +"/.geeknote"
from geeknote.geeknote import GeekNote

profile_path = '.ensync'
if not os.path.isdir('.ensync'):os.mkdir(profile_path)
def_logpath = os.path.join(profile_path, 'ensync.log')
    
formatter = logging.Formatter('%(asctime)-15s : %(message)s')
file_handler = logging.FileHandler(def_logpath)
file_handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

logger.addHandler(file_handler)

consoleHandler = logging.StreamHandler(sys.stdout)
consoleHandler.setFormatter(formatter)
logger.addHandler(consoleHandler)

user_info = GeekNote().getUserInfo()
os_encoding = locale.getpreferredencoding()

remote_tag_list = GeekNote().findTags()
remote_tag_dict = {tag.guid:tag.name for tag in remote_tag_list}

remote_notebooks = [notebook for notebook in GeekNote().findNotebooks()]
notebook_names = {notebook.guid:notebook.name for notebook in remote_notebooks}
notebook_guids = {notebook.name:notebook.guid for notebook in remote_notebooks}

account_info = {"site":geeknote.config.USER_BASE_URL, "user_name":user_info.name.decode("utf-8"), "id":user_info.id}

local_folders = {}#{"folder1":{'id1':'name1','id2':'name2'},"folder2":{'id3':"name3",'id4':"name4"}}
stored_notebooks = {}

notebook_json_path = os.path.join(profile_path, "notebook.json")
if (os.path.isfile(notebook_json_path)):    
    local_folders = json.load(open(notebook_json_path))
    
def update_stored_notebooks():
    for folder, nbs in local_folders.items():
        for guid, name in nbs.items():
            stored_notebooks[guid] = {"folder":folder,"notebook":name}
            
update_stored_notebooks()

class StorageJson (object):
    def __init__(self, path):
        self.path = path
        self.id_dict = {}
        #json struct{"local1":{'id1':'remote1','id2':'remote2'},"local2":{'id3':"remote3",'id4':"remote4"}}
        if (os.path.isfile(path)):    
            self._json = json.load(open(path))
        else:
            self._json = {}
        self._update_id_dict()    
    def _update_id_dict(self):
        for local_name, remote_objs in self._json.items():
            for guid, name in remote_objs.items():
                self.id_dict[guid] = {"local":local_name,"remote":name}    
    def sync_with_remote(self, remote_obj_list):
        for remote_obj in remote_obj_list:
            if remote_obj.guid not in self.id_dict:
                if remote_obj.name not in self._json:
                    self._json[remote_obj.name] = {remote_obj.guid:remote_obj.name}
            else:
                local_obj = self.id_dict[remote_obj.guid]
                if remote_obj.name != local_obj["remote"] and local_obj["local"] == local_obj["remote"]:
                    old_name = local_obj["local"]
                    self._json[old_name][remote_obj.guid] = remote_obj.name
                    self._json[remote_obj.name] = self._json.pop(old_name)
        self._update_id_dict()
        json.dump(self._json,open(self.path,"w+"), indent=4)
                    
    
tag_json = StorageJson(os.path.join(profile_path, "tag.json"))
tag_json.sync_with_remote(remote_tag_list)
print tag_json._json        
# print json.dumps(local_folders, indent=4)             
# print json.dumps(stored_notebooks, indent=4)     
 
for nb in remote_notebooks:
    if nb.guid not in stored_notebooks:
        if nb.name not in local_folders:
            #add new folder
            local_folders[nb.name] = {nb.guid:nb.name}
    else:
        local_nb = stored_notebooks[nb.guid]
        if nb.name != local_nb["notebook"] and local_nb["folder"] == local_nb["notebook"]:
            #notebook name is updated in server
            old_name = local_nb["notebook"]
            print ("notebook {} rename to {}".format(old_name, nb.name))
            local_folders[old_name][nb.guid] = nb.name
            local_folders[nb.name] = local_folders.pop(old_name)
            os.rename(old_name, nb.name)
    
update_stored_notebooks()  
# print json.dumps(local_folders, indent=4)             
# print json.dumps(stored_notebooks, indent=4)       

json.dump(local_folders,open(notebook_json_path,"w+"), indent=4)
#sys.exit()

def log(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception, e:
            print e
            traceback.print_exc()
            logger.error("%s", str(e))
    return wrapper


def _list_member(obj):
    for attr_name, attr in obj.__dict__.items():    
        print (attr_name, type(attr), attr)


class LocalNote(object):
    @log
    def __init__(self, path):
        self.path = path
        if not (os.path.isfile(path)):   
            raise ValueError('file not exist:' + path)
        try:           
            root = self.get_elm()
        except Exception, e:
            logger.error ("XML parse fail with file: {}".format(path))
            logger.error("%s", str(e))
            print ("XML parse fail with file: {}".format(path))
            return
        self.title = unescape(root.find('note/title').text)
        self.last_update = 0
        self.account_info = None
        self.tagid_dict = {}
        account_list = root.findall("server_info/account")
        tag_elm_dict = {i.text:i for i in root.findall('note/tag')}
        if (len(account_list) > 0):
            for account_elm in account_list:
                last_update = int(account_elm.find('last_update').text)
                if last_update > self.last_update:
                    self.last_update = last_update
                if (account_elm.find('base_site').text == geeknote.config.USER_BASE_URL 
                and account_elm.find('user_id').text == str(user_info.id)):
                    self.account_info = xmltodict.parse(ET.tostring(account_elm))['account']
#                     print json.dumps(self.account_info, indent=2)
                    srv_elm_tag_dict = {i.attrib['id']:i for i in account_elm.findall("tag")}
                    self.tagid_dict = {i.attrib['id']:i.text for i in account_elm.findall("tag")}
                    srv_info_updated = False

                    if len(srv_elm_tag_dict) > 0:
                        for guid, elm in srv_elm_tag_dict.items():
                            if remote_tag_dict[guid] != elm.text:
                                logger.info("tag {} is changed to {}".format(elm.text, remote_tag_dict[guid]))
                                tag_elm_dict[elm.text].text = remote_tag_dict[guid]
                                elm.text = remote_tag_dict[guid]
                                srv_info_updated = True
                    
                    remote_nb_name = notebook_names[self.account_info['notebook_id']]
                    if self.account_info['notebook_name'] != remote_nb_name:
                        account_elm.find('notebook_name').text = remote_nb_name
                        srv_info_updated = True
                        
                    if srv_info_updated:
                        os.remove(path)
                        elm_to_file(root)

    def get_elm(self):
        parser = ET.XMLParser(strip_cdata=False)
        return ET.parse(self.path, parser=parser)               
    def change_notebook(self, notebook_id):
        old_path = self.path
        export_elm = self.get_elm()
        for i in export_elm.findall("server_info/account"):
            if i.find('note_id').text == self.account_info['note_id']:                
                i.find("notebook_id").text = notebook_id
                i.find("notebook_name").text = notebook_names[notebook_id]
        self.path = elm_to_file(export_elm)
        os.remove(old_path)

        
@log        
def remote_note_to_et(note_meta):
    note_obj = GeekNote().getNote(note_meta.guid, withContent=True, 
                                    withResourcesData=True, withResourcesRecognition=True)
                      
    export_elm = ET.Element('en-export')
    note_elm = SubElement(export_elm, 'note')
    title_elm = SubElement(note_elm, 'title')
    title_elm.text = escape(note_meta.title.decode('utf-8'))#.replace('&', '&amp;')
    content_elm = SubElement(note_elm, "content")
    content_elm.text = ET.CDATA(note_obj.content.decode('utf-8'))
    SubElement(note_elm, 'created').text = strftime("%Y%m%dT%H%M%SZ", gmtime(note_meta.created / 1000))
    SubElement(note_elm, 'updated').text = strftime("%Y%m%dT%H%M%SZ", gmtime(note_meta.updated / 1000))
    
    if note_obj.tagGuids is not None:
        for tag_id in note_obj.tagGuids:
            SubElement(note_elm, 'tag').text = escape(remote_tag_dict[tag_id].decode('utf-8'))#.replace('&', '&amp;')
            
    note_attr_elm = SubElement(note_elm, 'note-attributes')

#     upper_regex = re.compile("[A-Z]")
    first_cap_re = re.compile('(.)([A-Z][a-z]+)')
    all_cap_re = re.compile('([a-z0-9])([A-Z])')
    def _conv_export_name(name):
        s1 = first_cap_re.sub(r'\1_\2', name)
        return all_cap_re.sub(r'\1_\2', s1).lower()
                
    for attr_name, attr in note_meta.attributes.__dict__.items():
        if attr != None:
            attr_name = _conv_export_name(attr_name)
            if isinstance(attr, basestring):
                SubElement(note_attr_elm, attr_name).text = escape(attr.decode("utf-8"))#.replace('&', '&amp;')
            if isinstance(attr, long):
                if ('time' in attr_name or 'date' in attr_name):
                    SubElement(note_attr_elm, attr_name).text = strftime("%Y%m%dT%H%M%SZ", gmtime(attr / 1000))
                else:
                    SubElement(note_attr_elm, attr_name).text = str(attr)     
            if isinstance(attr, (float, bool)):
                SubElement(note_attr_elm, attr_name).text = str(attr)   
                     
    if (note_meta.largestResourceSize) is not None:
        # print ("largest resource size of {} = {} ".format(print_title, note.largestResourceSize))
        for res in note_obj.resources:
            res_elm = SubElement(note_elm, 'resource')
            # print ("found resource with size {} in {} ".format(res.data.size, print_title))
            SubElement(res_elm, 'data', encoding="base64").text = str(base64.b64encode(res.data.body))
            SubElement(res_elm, 'mime').text = res.mime
            if res.width != None: SubElement(res_elm, 'width').text = str(res.width)                 
            if res.height != None:SubElement(res_elm, 'height').text = str(res.height)
            if res.recognition != None:SubElement(res_elm, 'recognition').text = ET.CDATA(res.recognition.body.decode("utf-8")) 
            res_attr_elm = SubElement(res_elm, 'resource-attributes')
                               
            for attr_name, attr in res.attributes.__dict__.items():
                if attr == None:
                    continue
                attr_name = _conv_export_name(attr_name)
                if isinstance(attr, basestring):
                    attr = attr.decode("utf-8")
                    SubElement(res_attr_elm, attr_name).text = escape(attr)#.replace('&', '&amp;')
                if ('time' in attr_name or 'date' in attr_name):
                    SubElement(res_attr_elm, attr_name).text = strftime("%Y%m%dT%H%M%SZ", gmtime(attr / 1000))
                if isinstance(attr, (float, bool)):
                    SubElement(res_attr_elm, attr_name).text = str(attr)
    notebook_name = escape(notebook_names[note_obj.notebookGuid])
    server_elm = SubElement(export_elm, 'server_info')
    account_elm = SubElement(server_elm, 'account')
    SubElement(account_elm, "note_id").text = note_meta.guid
    SubElement(account_elm, "notebook_name").text = notebook_name
    SubElement(account_elm, "notebook_id").text = note_obj.notebookGuid
    SubElement(account_elm, "user_id").text = str(user_info.id)
    SubElement(account_elm, "user_full_name").text = user_info.name.decode("utf-8")
    SubElement(account_elm, "base_site").text = geeknote.config.USER_BASE_URL
    SubElement(account_elm, "last_update").text = str(note_meta.updated)
    if note_obj.tagGuids is not None:
        for tag_id in note_obj.tagGuids:
            SubElement(account_elm, 'tag',attrib={'id':tag_id}).text = escape(remote_tag_dict[tag_id].decode('utf-8'))#.replace('&', '&amp;')f
    return export_elm

def elm_to_file( export_elm ):
    notebook_name = unescape(export_elm.find('server_info/account/notebook_name').text)
    note_title = unescape(export_elm.find('note/title').text)
    if not os.path.isdir(notebook_name):
        os.mkdir(notebook_name)
    file_idx = 0
    file_idx_str = ''
    while True:
        export_path = notebook_name + '/' + note_title[:32].replace('/', '-') + file_idx_str + '.enex'
        if not os.path.isfile(export_path):
            break
        file_idx += 1
        file_idx_str = '_' + str(file_idx)
    enex_file = open( export_path , "wb+")
    enex_file.write(unescape(ET.tostring(export_elm, xml_declaration=True,encoding='utf-8', pretty_print=True, 
                                         doctype='<!DOCTYPE en-export PUBLIC "SYSTEM" "http://xml.evernote.com/pub/evernote-export3.dtd">')))
    enex_file.truncate()
    logger.info("Export note: " + export_path)
    return export_path
    
if __name__ == "__main__":
    
    logger.info("Start ensync in: {}".format(os.path.abspath('.')))
    logger.info('Account info: site:{}, account:{}, id:{}'.format(geeknote.config.USER_BASE_URL, user_info.name.decode("utf-8"), user_info.id))
    gn = GeekNote()
    local_notes = {}
    for path, dirs, files in os.walk("."):
        for f in files:
            if f.endswith('.enex'):
                note_obj = LocalNote(path +'/' + f)
                local_notes[note_obj.account_info['note_id']] = note_obj
    remote_notes = gn.findNotes("*", EDAM_USER_NOTES_MAX).notes
    for remote_note in remote_notes:
        if local_notes.has_key(remote_note.guid):
            local_note = local_notes[remote_note.guid]
            update_required = False
            if remote_note.updated > local_note.last_update:
                update_required = True
            if remote_note.tagGuids == None:
                remote_tag_qty = 0
            else:
                remote_tag_qty = len(remote_note.tagGuids)
            if len(local_note.tagid_dict) != remote_tag_qty:
                update_required = True
            elif remote_tag_qty > 0:
                for guid in remote_note.tagGuids:
                    if not local_note.tagid_dict.has_key(guid):
                        update_required = True
            if update_required == True:               
                logger.info('Update note: ' + local_note.path)
                os.remove(local_note.path)
                export_elm = remote_note_to_et(remote_note)
                elm_to_file(export_elm)
            elif remote_note.notebookGuid != local_note.account_info["notebook_id"]:
                logger.info(local_note.path + ' have change notebook from {} to {}'.format(local_note.account_info["notebook_id"], remote_note.notebookGuid))                
                local_note.change_notebook(remote_note.notebookGuid)              
        else:
            logger.info('download new note: ' + remote_note.title)
            root = remote_note_to_et(remote_note)
            elm_to_file(root)
    # remote_notebooks = [notebook.name for notebook in gn.findNotebooks()]
    # print remote_notebooks
