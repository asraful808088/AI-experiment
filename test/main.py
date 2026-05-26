import ollama
import time
import sys
import platform
import os
from experiment.test.getPcConfig import cfg
from experiment.test.workers import Workers
from experiment.test.modelBlock import codeModelBlock001







# def get_system_info():
#     return cfg
# #  {'role': 'system', 'content': get_system_info()},
# print('whats you opnn')
# prompt = input()
# stream = ollama.chat(
#     model='qwen2.5-coder:3b',
#     messages=[
       
#         {'role': 'user', 'content': prompt}
#     ],
#     stream=True
# )
# full_response = ""
# char_count = 0
# start_time = time.time()
# for chunk in stream:
#     content = chunk['message']['content']
#     print(content, end='', flush=True)
#     full_response += content
#     char_count += len(content)
# elapsed = time.time() - start_time
# print("\n" + "─"*50)
# print(f"✅ Complete! {char_count} chars in {elapsed:.1f} seconds")



def outOfCodingWork(): ## that means 
    pass
           


def codingWorker(user_system_prompt='',history=[]):
    # DESIITOIN MAKE FROM  user_system_prompt WHIICH FUNCTIONN USE IT using json
    stream = codeModelBlock001(user_system_prompt,h=history) # return json.... json return worker name 
    worker_obj = Workers()
    result  = worker_obj.allFunctonalTools # return will be result
    if result['return_type_str']:
        # dicition to next stap or send information to user
        if True:
            pass
        else:
            pass
        pass
    else:
         # dicition to next stap or send information to user
        pass

    pass








def startbot(runtimeprompt='',history=[]):
    # disition is work for coding by json 
    codingWorker(runtimeprompt)


