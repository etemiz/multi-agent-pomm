# Agent based on regular FC network with replay

from pommerman.agents import BaseAgent
from pommerman        import constants
from pommerman        import utility

import math
import random

from collections import namedtuple
from itertools   import count

import numpy as np

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torchvision.transforms as T

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(device)

Transition = namedtuple('Transition', ('state', 'action', 'next_state', 'reward'))

class _utils:
    def __init__(self, board_h, board_w):
        self.board_area = board_h * board_w 

        self.int2vec = {
            1  : np.zeros((self.board_area,)),
            2  : np.zeros((self.board_area,)),
            4  : np.zeros((self.board_area,)),
            6  : np.zeros((self.board_area,)),
            7  : np.zeros((self.board_area,)),
            8  : np.zeros((self.board_area,)),
            10 : np.zeros((self.board_area,)),
            11 : np.zeros((self.board_area,)),
            12 : np.zeros((self.board_area,)),
            13 : np.zeros((self.board_area,))
        }
        self.blast_strength_vec = np.zeros((max(board_h, board_w)+1,))

        self.max_ammo = 4
        self.ammo = np.zeros((self.max_ammo,))

        self.this_agent = np.zeros((5,))
        self.friend = np.zeros((5,))
        self.enemy1 = np.zeros((5,))
        self.enemy2 = np.zeros((5,))
        self.enemy3 = np.zeros((5,))

        # Different symbolic objects
        self.input_size = self.board_area*len(self.int2vec) + \
            max(board_h, board_w)+1 + \
            self.max_ammo + \
            5*5 + \
            self.board_area + \
            self.board_area


   
    def input(self, obs):
        blast_strength = int(obs['blast_strength'])
        ammo        = int(obs['ammo'])
        my_position = tuple(obs['position'])
        teammate    = int(obs['teammate'].value) - 9
        enemies     = np.array([e.value for e in obs['enemies']]) - 9
        board       = np.array(obs['board'])
        bombs       = np.array(obs['bomb_blast_strength'])/2.0
        bombs_life  = np.array(obs['bomb_life'])/9.0
        
        # Symbolic objects to vector of boards
        for idx, cell in enumerate(board.flatten().tolist()):
            if cell in self.int2vec:
                self.int2vec[cell][idx] = 1.0
        
        # !TODO Test this assumption
        self.blast_strength_vec[blast_strength] = 1.0

        # If ammo > 10, ammo = 10 (as one hot)
        self.ammo[min(self.max_ammo,ammo)-1] = 1.0

        agent_ids = [0,1,2,3,4]
        # Agents
        for an_enemy_id, an_enemy_vec in zip(enemies, [self.enemy1, self.enemy2, self.enemy3]):
            an_enemy_vec[an_enemy_id] = 1.0
            agent_ids.remove(an_enemy_id)
        self.friend[teammate] = 1.0 
        agent_ids.remove(teammate)
        # DEBUG
        if len(agent_ids) != 1: raise ValueError('Error! agent_ids has more/less than one id left!')
        # DEBUG
        self.this_agent[agent_ids[0]] = 1.0


        # !TODO Concatenate all the vectors 
        input_data = np.array([])
        for idx in self.int2vec:
            input_data = np.concatenate((input_data, self.int2vec[idx]))

        input_data = np.concatenate((input_data, self.blast_strength_vec))
        input_data = np.concatenate((input_data, self.ammo))
        input_data = np.concatenate((input_data, self.this_agent))
        input_data = np.concatenate((input_data, self.friend))
        input_data = np.concatenate((input_data, self.enemy1))
        input_data = np.concatenate((input_data, self.enemy2))
        input_data = np.concatenate((input_data, self.enemy3))
        input_data = np.concatenate((input_data, bombs.flatten()))
        input_data = np.concatenate((input_data, bombs_life.flatten()))
       
        #print("Data vector: {} v.s. input_size: {}".format(input_data.shape, self.input_size))

        return input_data.flatten()

        


        

            


class _ReplayMemory(object):
    def __init__(self, capacity):
        self.capacity = capacity
        self.memory = []
        self.position = 0
        EPS_START = 0.9
        EPS_END = 0.05
        EPS_DECAY = 200
    def push(self, *args):
        """Saves a transition."""
        if len(self.memory) < self.capacity:
            self.memory.append(None)
        self.memory[self.position] = Transition(*args)
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)

    def __len__(self):
        return len(self.emory)


class FCAgent(BaseAgent):
    def __init__(self, board_h=11, board_w=11, *args, **kwargs):
        super(FCAgent, self).__init__(*args, **kwargs)
        # Common functionalities among learning agents
        self.utils = _utils(board_h, board_w)
        self.input_size = self.utils.input_size

        # Network -----------------------------------------------------------------------
        N, D_in, H1, H2, D_out = 1, self.input_size, 128, 64, 6

        self.model = torch.nn.Sequential(
            torch.nn.Linear(D_in, H1),
            torch.nn.ReLU(),
            torch.nn.Linear(H1, H2),
            torch.nn.ReLU(),
            torch.nn.Linear(H2, D_out),
        )
        self.loss = torch.nn.CrossEntropyLoss()
        self.learning_rate = 1e-4
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.learning_rate)
        #--------------------------------------------------------------------------------
        
        self.step_num = 0
        self.policy_net = self.model.to(device)
        self.target_net = self.model.to(device)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()

        self.optimizer = optim.RMSprop(self.policy_net.parameters())
        self.memory = _ReplayMemory(10000)

        # Hyper Params ------------------------------------------------------------------
        self.BATCH_SIZE = 128
        self.GAMMA = 0.999
        self.EPS_START = 0.9
        self.EPS_END = 0.05
        self.EPS_DECAY = 20_0
        self.TARGET_UPDATE = 10
        #--------------------------------------------------------------------------------

        self.episode_durations = []

    #def optimize_model():
    def _train(self):
        if len(self.memory) < self.BATCH_SIZE:
            return
        transitions = self.memory.sample(self.BATCH_SIZE)
        # Transpose the batch (see http://stackoverflow.com/a/19343/3343043 for
        # detailed explanation).
        batch = Transition(*zip(*transitions))

        # Compute a mask of non-final states and concatenate the batch elements
        non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                              batch.next_state)), device=device, dtype=torch.uint8)
        non_final_next_states = torch.cat([s for s in batch.next_state
                                                    if s is not None])
        state_batch  = torch.cat(batch.state)
        action_batch = torch.cat(batch.action)
        reward_batch = torch.cat(batch.reward)

        # Compute Q(s_t, a) - the model computes Q(s_t), then we select the
        # columns of actions taken
        state_action_values = self.policy_net(state_batch).gather(1, action_batch)

        # Compute V(s_{t+1}) for all next states.
        next_state_values = torch.zeros(self.BATCH_SIZE, device=device)
        next_state_values[non_final_mask] = self.target_net(non_final_next_states).max(1)[0].detach()
        # Compute the expected Q values
        expected_state_action_values = (next_state_values * GAMMA) + reward_batch

        # Compute Huber loss
        loss = F.smooth_l1_loss(state_action_values, expected_state_action_values.unsqueeze(1))

        # Optimize the model
        self.optimizer.zero_grad()
        loss.backward()
        for param in self.policy_net.parameters():
            param.grad.data.clamp_(-1, 1)
        self.optimizer.step()
    
    def _select_action(self, state):
        sample = random.random()
        eps_threshold = self.EPS_END + (self.EPS_START - self.EPS_END) * \
            math.exp(-1. * self.step_num / self.EPS_DECAY)
        self.step_num += 1
        if sample > eps_threshold:
            with torch.no_grad():
                #return self.policy_net(state).max(1)[1].view(1, 1)
                return self.policy_net(state.to(device)).max(0)[1].view(1, 1)
        else:
            #print(torch.tensor([[random.randrange(6)]], device=device, dtype=torch.long))
            return torch.tensor([[random.randrange(2)]], device=device, dtype=torch.long)
    
    
    def act(self, obs, action_space):
        
        # Do the input
        # !TODO
        x_np = self.utils.input(obs) 

        x_torch = torch.Tensor(x_np)

        #y_pred = self.model(x)
        #loss = loss_fn(y_pred, y)

        # Initialize the environment and state
        #env.reset()
        #last_screen = get_screen()
        #current_screen = get_screen()
        #state = current_screen - last_screen
        #for t in count():
        # Select and perform an action
        action = self._select_action(x_torch)

        """
        _, reward, done, _ = env.step(action.item())

        # Observe new state
        #last_screen    = current_screen
        #current_screen = get_screen()
        if not done:
            next_state = current_screen - last_screen
        else:
            next_state = None

        # Store the transition in memory
        self.memory.push(state, action, next_state, reward)

        # Move to the next state
        state = next_state

        # Perform one step of the optimization (on the target network)
        _train()
        if done:
            self.episode_durations.append(t + 1)
            #plot_durations()
            #break"""
        return action.cpu().numpy()[0][0]
        #return constants.Action.Down.value
            
    def episode_end(self, reward): 
        # Update the target network
        """if i_episode % TARGET_UPDATE == 0:
            target_net.load_state_dict(policy_net.state_dict())"""
        pass



if __name__ == '__main__':
    import pommerman
    from pommerman import agents
    agent_list = [FCAgent(), agents.SimpleAgent(), agents.RandomAgent(), agents.SimpleAgent()]
    env = pommerman.make('PommeTeamCompetition-v0', agent_list)
    
    state = env.reset()
    done = False
    max_vals_bbs = []
    max_vals_bl  = []
    while not done:
        #env.render()
        actions = env.act(state)
        state, reward, done, info = env.step(actions)

        # Figure the range 
        for an_obs in state:
            max_vals_bbs.append( max(an_obs['bomb_blast_strength'].flatten().tolist()) )
            max_vals_bl.append( max(an_obs['bomb_life'].flatten().tolist()) )
        
        #print(reward)
    print("bomb_blast_strength max: ", max(max_vals_bbs))
    print("bomb_life max: ", max(max_vals_bl))
    env.close()
    print(info)
