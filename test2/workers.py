from fileReader import gotFileWithInfo,read_file,write_file
from networkFetcher import getNetworkInfo
from vue_code_distributor import vueBlockBuilder
class Workers:
    DIR_READER_WITH_PATH:str = 'DIR_READER_WITH_PATH'
    NETWORK_FETCHER:str = 'NETWORK_FETCHER'
    VUE_BLOCK_BUILDER:str = 'VUE_BLOCK_BUILDER'
    READ_FILE:str = 'READ_FILE'
    WRITE_FILE:str = 'WRITE_FILE'
    workers_obj_list = [
        {"name":DIR_READER_WITH_PATH,'worker':gotFileWithInfo,'details':'','return_type_str':True},
        {"name":NETWORK_FETCHER,'worker':getNetworkInfo,'details':'','return_type_str':True},
        {"name":VUE_BLOCK_BUILDER,'worker':vueBlockBuilder,'details':'','return_type_str':True},
        {"name":READ_FILE,'worker':read_file,'details':'','return_type_str':True},
        {"name":WRITE_FILE,'worker':write_file,'details':'','return_type_str':False},
    ]
    def allFunctonalTools(self,workername,paramitters={}):
        for item in self.workers_obj_list:
            if item['name'] == workername:
                return {"result":item['worker'](paramitters),'return_type_str':item['return_type_str']}
                



