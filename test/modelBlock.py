import ollama
def codeModelBlock001(prompt,h=[]):
     his = h
     his.append([{'role': 'user', 'content': prompt}])
     stream = ollama.chat(
     model='qwen2.5-coder:3b',
     messages=h,stream=True)
     return stream