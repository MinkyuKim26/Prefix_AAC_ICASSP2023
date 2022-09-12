from transformers import AdamW, get_cosine_schedule_with_warmup
from tqdm import tqdm
import time
import datetime

import torch
import torch.nn as nn
from torch.nn import functional as nnf

# 평가를 위해 필요한 것들
from typing import MutableMapping, MutableSequence,\
    Any, Union, List, Dict, Tuple
from eval_metrics import evaluate_metrics
from terminaltables import AsciiTable
import pickle

USE_CUDA = torch.cuda.is_available() 
device = torch.device('cuda:0' if USE_CUDA else 'cpu')

def Train(model, LR, train_dataloader, test_dataloader, epochs, model_name, beam_search, Dataset = 'AudioCaps') :
    
    model.train()
    model.to(device)
    
    warmup_steps = int((epochs * len(train_dataloader)) /6) # 총 weight update 횟수의 1/6은 warm-up 시기임
    
    optimizer = AdamW(model.parameters(), lr=LR, weight_decay = 0.01)
    
    scheduler = get_cosine_schedule_with_warmup(
        optimizer, num_warmup_steps=warmup_steps, num_training_steps=epochs * len(train_dataloader))
    
    epoch_eval_interval = int(epochs/3)
    
    prefix_length = model.audio_prefix_length + model.semantic_prefix_length
    
    training_consumed_sec = 0
    
    for epoch in range(epochs) :
        pbar = tqdm(train_dataloader, desc=f"Training Epoch {epoch}")
        total_loss_per_epopch = 0.0
        loss_add_count = 0.0
        
        train_start_time_per_epoch = time.time()
        
        for batch_i, (audio, tokens, mask, _) in enumerate(pbar) :
            
            audio = audio.to(device)
            tokens = tokens.to(device)
            mask = mask.to(device)
            
            logits = model(audio, tokens, mask)[:, prefix_length - 1: -1]
                
            loss = nnf.cross_entropy(logits.reshape(-1, logits.shape[-1]).to(device), tokens.flatten().to(device), ignore_index=0)
                
            total_loss_per_epopch += loss.item()
            loss_add_count += 1.0
    
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
                
            scheduler.step()
            
            avr_loss = total_loss_per_epopch / loss_add_count
            pbar.set_description(f"Training Epoch {epoch}, Loss = {round(avr_loss, 5)}")
        
        training_consumed_sec += (time.time() - train_start_time_per_epoch)
        
        
        
        if (epoch >= 9) and ((epoch + 1) % 5 == 0) : 
            eval_model(model, test_dataloader, epoch, model_name, beam_search, Dataset = Dataset)
            model.train()
            
        if (epoch + 1 == 16) and Dataset == 'AudioCaps' :
            for param in model.audio_encoder.parameters():
                param.requires_grad = False
        # 학습의 2/3 지점 이후부터 Encoder의 모든 Parameter들을 학습 가능하게
        
        elif Dataset == 'Clotho' :
            
            if (epoch + 1 == 30) :
                for param in model.audio_encoder.parameters():
                    param.requires_grad = False
#             elif (epoch + 1 == 35) :     
#                 for param in model.gpt.parameters():
#                     param.requires_grad = False
            
                    
        param_file_path = "./Train_record/params_" + model_name + "/Param_epoch_" + str(epoch) + ".pt"
            
        torch.save(model.state_dict(), param_file_path)

    result_list = str(datetime.timedelta(seconds=training_consumed_sec)).split(".")
    print()
    print("Training time :", result_list[0])


def eval_model(model, test_dataloader, epoch, model_name, beam_search, Dataset = 'AudioCaps') :
    
    model.eval()
    model.to(device)

    # 모아놨다가 한 번에 평가하자
    captions_pred: List[Dict] = []
    captions_gt: List[Dict] = []
    
    for i, (audio, captions, f_names) in enumerate(tqdm(test_dataloader, desc="Eval...")):
        with torch.no_grad():
            # 하나의 raw audio에 대해 5개의 caption이 등장
            
            # Test dataset은 audio, caption의 비율이 1:5다 
            # Batch size를 5로 설정했음. 0번 인덱스 값만 사용할거임
            audio = audio.to(device)
            
            audio = audio[0,:].unsqueeze(0)
            
            if beam_search == True :
                pred_caption = model(audio, None, beam_search = True)[0][0]
            else :
                pred_caption = model(audio, None, beam_search = False)[0]

        captions_pred.append({
                        'file_name': f_names[0], 
                        'caption_predicted': pred_caption})
        captions_gt.append({
                        'file_name': f_names[0],
                        'caption_1': captions[0],
                        'caption_2': captions[1],
                        'caption_3': captions[2],
                        'caption_4': captions[3],
                        'caption_5': captions[4]})
    
    # 전체 측정값을 한 번에 method에 넣어서 측정
    metrics = evaluate_metrics(captions_pred, captions_gt)
    
    total_results = {}
    total_results['BLUE_1'] = metrics['bleu_1']['score']
    total_results['BLUE_2'] = metrics['bleu_2']['score']
    total_results['BLUE_3'] = metrics['bleu_3']['score']
    total_results['BLUE_4'] = metrics['bleu_4']['score']
    total_results['METEOR'] = metrics['meteor']['score']
    total_results['ROUGE_l'] = metrics['rouge_l']['score']
    total_results['CIDEr'] = metrics['cider']['score']
    total_results['SPICE'] = metrics['spice']['score']
    total_results['SPIDEr'] = metrics['spider']['score']  
    
    print("total result")
    print(AsciiTable(
                    [
                        ["Type", "Value"],
                        ["BLEU_1", format(round(float(total_results['BLUE_1']), 6), 'f')],
                        ["BLEU_2", format(round(float(total_results['BLUE_2']), 6), 'f')],
                        ["BLEU_3", format(round(float(total_results['BLUE_3']), 6), 'f')],
                        ["BLEU_4", format(round(float(total_results['BLUE_4']), 6), 'f')],
                        ["METEOR", format(round(float(total_results['METEOR']), 6), 'f')],
                        ["ROUGE_l", format(round(float(total_results['ROUGE_l']), 6), 'f')],
                        ["CIDEr", format(round(float(total_results['CIDEr']), 6), 'f')],
                        ["SPICE", format(round(float(total_results['SPICE']), 6), 'f')],
                        ["SPIDEr", format(round(float(total_results['SPIDEr']), 6), 'f')]
                    ]).table)    

    # 결과 저장 
    result_file_path = './eval_result/epoch_' + str(epoch) + '_' + model_name + '.pkl' 
    with open(result_file_path,'wb') as f:
        pickle.dump(total_results, f)