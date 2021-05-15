import argparse, os, numpy as np, torch, json 

from itertools import count
from tensorboardX import SummaryWriter
from pathlib import Path 
from pettingzoo.mpe import simple_spread_v2 
from pettingzoo.sisl import multiwalker_v6 

from hammer import PPO 
from utils import read_config 


def run(args): 
    if args.envname == "cn": 
        env = simple_spread_v2.parallel_env(N=args.nagents, local_ratio=0.5, max_cycles=args.maxcycles) 
        env.reset()
        agents = [agent for agent in env.agents] 
        obs_dim = env.observation_spaces[env.agents[0]].shape[0]
        action_dim = env.action_spaces[env.agents[0]].n 
        agent_action_space = env.action_spaces[env.agents[0]] 
    
    elif args.envname == "mw": 
        env = multiwalker_v6.parallel_env(n_walkers=args.nagents) 
        env.reset()
        agents = [agent for agent in env.agents] 
        obs_dim = env.observation_spaces[env.agents[0]].shape[0]        
        action_dim = env.action_spaces[env.agents[0]].shape[0] 
        agent_action_space = env.action_spaces[env.agents[0]]


    config = read_config(args.config) 
    if not config:
        print("config required")
        return
    
    random_seed = args.randomseed 
    if random_seed:
        print("Random Seed: {}".format(random_seed))
        env.seed(random_seed) 
        torch.manual_seed(random_seed)
        np.random.seed(random_seed)

    name = "--".join([
        "env_" + args.envname, 
        "n_" + str(args.nagents), 
        "dru_" + str(args.dru_toggle), 
        "meslen_" + str(args.meslen), 
        "sharedparams_" + str(args.sharedparams), 
        "randomseed_" + str(args.randomseed) 
    ]) 

    name = "eval--"+args.eval_path.split("/")[-3] if args.eval else name 
    
    expname = name if args.expname == None else args.expname 
    
    writer = SummaryWriter(logdir=os.path.join("./save/", expname, args.logdir)) 
    # log_dir = Path('./logs/HAMMER-gradient-analysis/')
    # for i in count(0):
    #     temp = log_dir/('run{}'.format(i)) 
    #     if temp.exists():
    #         pass
    #     else:
    #         writer = SummaryWriter(logdir=temp)
    #         log_dir = temp
    #         break

    betas = (0.9, 0.999)

    HAMMER = PPO(
        agents=agents,
        single_state_dim=obs_dim, 
        single_action_dim=action_dim,
        meslen = args.meslen, 
        n_agents=len(agents), # required for discrete messages
        lr=config["lr"],
        betas=betas,
        gamma = config["gamma"],
        K_epochs=config["K_epochs"],
        eps_clip=config["eps_clip"],        
        actor_layer=config["actor_layer"],
        critic_layer=config["critic_layer"], 
        dru_toggle=args.dru_toggle, 
        is_discrete=config["is_discrete"], 
        sharedparams=0
    ) 
    if args.eval: 
        HAMMER.load(args.eval_path)

    if args.dru_toggle: 
        print("Using DRU") 
    else: 
        print("Not Using DRU")

    # logging variables
    ep_reward = 0
    global_timestep = 0

    obs = env.reset() 

    i_episode = 0
    episode_rewards = 0 
    
    for timestep in count(1):
        actions, messages = HAMMER.policy_old.act(obs, HAMMER.memory, HAMMER.global_memory)  
        
        if args.envname == "mw": 
            actions = {agent : np.clip(actions[agent], agent_action_space.low, agent_action_space.high) for agent in agents}     
        next_obs, rewards, is_terminals, infos = env.step(actions) 

        HAMMER.memory_record(rewards, is_terminals)
        episode_rewards += np.mean(np.array(list(rewards.values()))) 

        # update if its time
        if (not args.eval) and (timestep % config["update_timestep"] == 0): 
            HAMMER.update()
            [mem.clear_memory() for mem in HAMMER.memory]
            HAMMER.global_memory.clear_memory()

        obs = next_obs

        # If episode had ended
        if all([is_terminals[agent] for agent in agents]):
            i_episode += 1 

            # recording episodic rewards per agent 
            writer.add_scalar('Episodic Reward', episode_rewards, i_episode) 
            
            # recording mean of messages of each agent 
            for i, agent in enumerate(agents): 
                for m in range(args.meslen): 
                    writer.add_scalar(str(agent)+"--"+ 'message_feature_'+str(m), np.mean(np.array(HAMMER.memory[i].messages).reshape((-1))), i_episode) 

            [mem.clear_messages() for mem in HAMMER.memory] 
            obs = env.reset() 
            print('Episode {} \t  Episodic reward per agent: {}'.format(i_episode, episode_rewards)) 
            episode_rewards = 0 

        # save every 50 episodes
        if (not args.eval) and (i_episode % args.saveinterval == 0):
            save_dir = os.path.join("./save/", expname, args.savedir, "checkpoint_ep_"+str(i_episode)) 
        
            if not os.path.exists(save_dir): 
                os.makedirs(os.path.join(save_dir))  
            HAMMER.save(save_dir) 

        if i_episode == args.maxepisodes: 
            if args.eval: 
                if not os.path.exists(os.path.join("./save/", expname, "data")):
                    os.makedirs(os.path.join("./save/", expname, "data"))
                for i, agent in enumerate(agents): 
                    np.save(os.path.join("./save/", expname, "data", "states_"+agent+".npy"), HAMMER.memory[i].states) 
                    np.save(os.path.join("./save/", expname, "data", "actions_"+agent+".npy"), HAMMER.memory[i].actions) 
                    np.save(os.path.join("./save/", expname, "data", "messages_"+agent+".npy"), HAMMER.memory[i].messages) 
            break

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default='configs/cn.yaml', help="config file name")

    parser.add_argument("--expname", type=str, default=None)
    parser.add_argument("--envname", type=str, default='cn')
    parser.add_argument("--nagents", type=int, default=3) 
    parser.add_argument("--eval", type=int, default=0) 
    parser.add_argument("--eval_path", type=str, default="") 

    parser.add_argument("--sharedparams", type=int, default=1) 

    parser.add_argument("--maxepisodes", type=int, default=100) 
    parser.add_argument("--maxcycles", type=int, default=25) 

    parser.add_argument("--dru_toggle", type=int, default=1) # 0 for HAMMERv2 and 1 for HAMMERv3 

    parser.add_argument("--meslen", type=int, default=1, help="message length")
    parser.add_argument("--randomseed", type=int, default=9)

    parser.add_argument("--saveinterval", type=int, default=25_000) 
    parser.add_argument("--logdir", type=str, default="logs/", help="log directory path")
    parser.add_argument("--savedir", type=str, default="model_checkpoints/", help="save directory path")
    

    args = parser.parse_args() 
    print(args.savedir)
    run(args=args) 
