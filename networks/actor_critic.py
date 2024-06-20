import numpy as np
import torch
import torch.nn.functional as F
from torch.distributions import Normal


def discount_rewards(r, gamma):
    discounted_r = torch.zeros_like(r)
    running_add = 0
    for t in reversed(range(0, r.size(-1))):
        running_add = running_add * gamma + r[t]
        discounted_r[t] = running_add
    return discounted_r


def bootstrapped_discount_rewards(r, gamma, done, next_values):
    bootstrapped_discounted_r = torch.zeros_like(r)
    running_add = 0
    for t in reversed(range(0, r.size(-1))):
        if done[t]:
            running_add = 0
        else:
            running_add = gamma * next_values[t]
        bootstrapped_discounted_r[t] = running_add + r[t]
    return bootstrapped_discounted_r

class Policy(torch.nn.Module):
    def __init__(self, state_space, action_space):
        super().__init__()
        self.state_space = state_space #dimension of state
        self.action_space = action_space
        self.hidden = 64
        self.tanh = torch.nn.Tanh()

        """
            Actor and Critic network
            -> same structure, only the final part changes
        """
        self.embedding_ac = torch.nn.Linear(state_space, 512)
        self.relu = torch.nn.ReLU()
        self.fc1_ac = torch.nn.Linear(512, 2048)
        self.lstm_ac = torch.nn.LSTM(2048, 1024, batch_first=True) #LSTM module specifies the dimension order of input tensors: (batch_size, sequence_length, input_size)
       
        self.fc2_actor = torch.nn.Linear(1024, action_space)
        self.fc2_critic = torch.nn.Linear(1024, 1)


    
        # Learned standard deviation for exploration at training time 
        self.sigma_activation = F.softplus
        init_sigma = 0.5
        self.sigma = torch.nn.Parameter(torch.zeros(self.action_space)+init_sigma)

        self.init_weights()


    def init_weights(self):
        for m in self.modules():
            if type(m) is torch.nn.Linear:
                torch.nn.init.normal_(m.weight)
                torch.nn.init.zeros_(m.bias)


    def forward(self, x):
        
        x = self.embedding_ac(x)
        x = torch.sum(x, dim=1)  # Summing over the observations
        x = self.relu(x)
        x = self.fc1_ac(x)
        x = self.relu(x)
        x, _ = self.lstm_ac(x.unsqueeze(0))  # Adding batch dimension for LSTM
        
        """
            Actor
        """
        action_mean = self.fc2_actor(x.squeeze(0))
        action_sigma = self.sigma_activation(self.sigma)

        normal_dist = Normal(action_mean, action_sigma)


        """
            Critic
        """
        value = self.fc3_critic(x.squeeze(0))
        
        return normal_dist,  #action_mean, action_sigma


class Agent(object):
    def __init__(self, policy, device='cpu'):
        self.train_device = device
        self.policy = policy.to(self.train_device)
        self.optimizer = torch.optim.Adam(policy.parameters(), lr=1e-3)

        self.gamma = 0.99
        self.states = []
        self.next_states = []
        self.action_log_probs = []
        self.rewards = []
        self.done = []


    def update_policy(self):
        action_log_probs = torch.stack(self.action_log_probs, dim=0).to(self.train_device).squeeze(-1)
        states = torch.stack(self.states, dim=0).to(self.train_device).squeeze(-1)
        next_states = torch.stack(self.next_states, dim=0).to(self.train_device).squeeze(-1)
        rewards = torch.stack(self.rewards, dim=0).to(self.train_device).squeeze(-1)
        done = torch.Tensor(self.done).to(self.train_device)

        self.states, self.next_states, self.action_log_probs, self.rewards, self.done = [], [], [], [], []

        # compute discounted returns
        discounted_returns = discount_rewards(rewards, self.gamma)
        
        #
        #   - compute boostrapped discounted return estimates
        #   - compute advantage terms
        #   - compute actor loss and critic loss
        #   - compute gradients and step the optimizer
        #
        _, values = self.policy(states)
        _, next_values = self.policy(next_states)

        # discounted_returns = bootstrapped_discount_rewards(rewards, self.gamma, done, next_values)
        advantages = discounted_returns - values.squeeze()

        actor_loss = -(action_log_probs * advantages.detach()).mean()
        # critic_loss = F.mse_loss(values.squeeze(), discounted_returns)

        critic_loss = F.pairwise_distance(values.squeeze(), discounted_returns.unsqueeze(1), p=2).mean()
        loss = actor_loss + critic_loss

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        return        


    def get_action(self, state, evaluation=False):
        """ state -> action (3-d), action_log_densities """
        x = torch.from_numpy(state).float().to(self.train_device)

        normal_dist, _ = self.policy(x)

        if evaluation:  # Return mean
            return normal_dist.mean, None

        else:   # Sample from the distribution
            action = normal_dist.sample()

            # Compute Log probability of the action [ log(p(a[0] AND a[1] AND a[2])) = log(p(a[0])*p(a[1])*p(a[2])) = log(p(a[0])) + log(p(a[1])) + log(p(a[2])) ]
            action_log_prob = normal_dist.log_prob(action).sum()

            return action, action_log_prob


    def store_outcome(self, state, next_state, action_log_prob, reward, done):
        self.states.append(torch.from_numpy(state).float())
        self.next_states.append(torch.from_numpy(next_state).float())
        self.action_log_probs.append(action_log_prob)
        self.rewards.append(torch.Tensor([reward]))
        self.done.append(done)

