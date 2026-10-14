"""Neutral participant language, separated from researcher-facing evidence."""
def participant_view(model_id, artifacts=False, replay=False, declared_only=False, replay_status=None, answer_recorded=True):
    if model_id=='openai':
        steps=[
            {'title':'Starting model','text':'OpenAI supplies the model through its service.','status':'Provider description','kind':'declared'},
            {'title':'Training material','text':'The provider describes its data practices. This experiment cannot inspect the complete training collection.','status':'Not fully visible','kind':'unknown'},
            {'title':'Training process','text':'The provider operates training. We cannot repeat that training from its internal records.','status':'Not replayed here','kind':'unknown'},
            {'title':'This answer','text':'We saved the model identifier, the question and the answer exactly as returned.','status':'Recorded by this lab','kind':'recorded'}]
        coverage='This view brings together the provider\'s published model information and the response recorded by this lab.'
    elif model_id=='open':
        steps=[
            {'title':'Starting model','text':'Qwen publishes downloadable model weights, a model card and a technical report.','status':'Provider description','kind':'declared'},
            {'title':'Training material','text':'The provider describes model development, but does not supply a complete item-by-item training history.','status':'Not fully visible','kind':'unknown'},
            {'title':'Local use','text':'We run the published model locally without further training.','status':'Unchanged baseline','kind':'recorded'},
            {'title':'This answer','text':'We saved which local model answered, along with the exact question and response.','status':'Recorded by this lab','kind':'recorded'}]
        coverage='This view starts with the published model weights and provider documentation, then records their local use.'
    else:
        checked='Checked locally' if artifacts else 'Checks pending'
        kind='checked' if artifacts else 'unknown'
        replay_label=('Reproduced on this machine' if replay else
                      'Replay incomplete' if replay_status=='budget-incomplete' else
                      'Replay differences recorded' if replay_status=='mismatch' else 'Replay pending')
        steps=[
            {'title':'Starting model','text':'This record begins with the published Qwen checkpoint. Information about its earlier development comes from the original provider.','status':'Published starting point','kind':'declared'},
            {'title':'Added books','text':'The local continuation uses 16 named books. We preserve the original text, source links and rights declarations, so the material can be traced back to its source.','status':checked,'kind':kind},
            {'title':'Text preparation','text':'We record which parts of each book were used, how text became model tokens, and which sections were kept separate for testing. A checker can rebuild those tokens from the original text.','status':checked,'kind':kind},
            {'title':'Training recipe','text':'We record the starting weights, training settings and software environment, then save each resulting checkpoint. The records connect the data, the procedure and the model changes.','status':checked,'kind':kind},
            {'title':'Repeat the work','text':'A separate process can repeat the recorded training from the starting model and compare the resulting weights. Its receipt lists the checkpoints compared and the result for each one.','status':replay_label,'kind':'checked' if replay else 'unknown'},
            {'title':'This answer','text':'We record which saved checkpoint answered and preserve the exact question and response.','status':'Recorded by this lab' if answer_recorded else 'Answer record pending','kind':'recorded' if answer_recorded else 'unknown'}]
        coverage='Follow the local development trail from the published Qwen checkpoint, through the added books and their processing, to training, verification and the model used here.'
    if declared_only:
        steps=[{**s,'status':'Developer statement','kind':'declared'} for s in steps]
        coverage='The developer provides this description. Checkable records are not shown in this condition. '+coverage
    return {'heading':'How this model was made',
            'intro':'Provenance is the model\'s development history: its starting point, the material added, how that material was processed, and the work that produced its checkpoints. Select a step to see what the record contains.',
            'steps':steps,'coverage':coverage,
            'what_it_means':'A matching file check tells us the recorded material has not changed. A successful training replay tells us the recorded procedure produced the same weights in the tested setup.',
            'record_boundary':'Earlier model development is described by Qwen. The detailed local records begin at that published checkpoint and follow the continuation performed by this project.',
            'who_checked':'Checks in this prototype run on the same local machine and are operated by the same project team. The records and procedures are available for others to inspect and repeat.',
            'reflection':'Which parts of this development history matter to your assessment of the model?'}
