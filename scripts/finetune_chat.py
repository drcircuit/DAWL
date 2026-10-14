"""Local Gradio chat for explicitly registered baseline and fine-tuned Ollama models."""
import argparse
import json
from pathlib import Path
import httpx
import gradio as gr

p=argparse.ArgumentParser();p.add_argument('--models',type=Path,required=True);p.add_argument('--port',type=int,default=8884)
p.add_argument('--ollama',default='http://127.0.0.1:11434');a=p.parse_args()
registry=json.loads(a.models.read_text());choices=[m['name'] for m in registry['models']]


def respond(message,history,model):
    if model not in choices: raise gr.Error('Choose a registered experiment checkpoint')
    messages=[]
    for item in history[-12:]:
        content=item.get('content')
        if isinstance(content,str) and item.get('role') in ('user','assistant'): messages.append({'role':item['role'],'content':content})
    messages.append({'role':'user','content':message})
    text=''
    with httpx.stream('POST',a.ollama+'/api/chat',json={'model':model,'messages':messages,'stream':True,'think':False,
         'options':{'temperature':0.7,'top_p':0.8,'top_k':20,'num_predict':384,'num_ctx':4096}},timeout=180,trust_env=False) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line: continue
            value=json.loads(line)
            if 'error' in value: raise gr.Error(value['error'])
            text+=value.get('message',{}).get('content','');yield text


with gr.Blocks(title='Hypertrix · Economics fine-tuning lab') as demo:
    gr.Markdown('# Hypertrix · Economics fine-tuning lab\nCompare the unchanged Qwen baseline with local training checkpoints. Responses come from model weights; no retrieval or corpus text is injected.')
    model=gr.Dropdown(choices=choices,value=choices[0],label='Checkpoint')
    gr.ChatInterface(respond,additional_inputs=[model],examples=[
        'Explain how division of labour can increase productivity according to Adam Smith.',
        'How does Ricardo distinguish rent from the cost of producing corn?',
        'What does Bagehot recommend a central bank do during a banking panic?'])
    gr.Markdown('Historical economic texts and a small experimental model. Compare claims with the sources. A fluent answer alone does not establish that fine-tuning improved factual accuracy.')
demo.queue(default_concurrency_limit=1).launch(server_name='127.0.0.1',server_port=a.port,share=False,inbrowser=False)
