
import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp
from scipy.optimize import minimize
import matplotlib.pyplot as plt
from datetime import timedelta, datetime
import datetime as dt


class SIR(object):
    def __init__(self,
                 country='Brazil',
                 N = 200e6,
                 infectedAssumption=1,
                 recoveredAssumption=1,
                 nth=1,
                 daysPredict=150,
                 estimateS0=False,
                 quarantineDate=None,
                 alpha=0.5,
                 forcedBeta=None,
                 forcedGamma=None,
                 betaBounds=(0.00000001, 2.0),
                 gammaBounds=(0.00000001, 2.0),
                 S0bounds=(10000, 10e6),
                 R0bounds=None,
                 hospRate=0.15,
                 daysToHosp=7,
                 daysToLeave=7,
                 opt='L-BFGS-B',
                 adjust_recovered=False
                 ):

        self.country = country
        self.N = N
        self.infectedAssumption = infectedAssumption  # Multiplier to account for non reported
        self.recoveredAssumption = recoveredAssumption
        self.nth = nth  # minimum number of cases to start modelling
        self.daysPredict = daysPredict
        self.quarantineDate = quarantineDate
        self.estimateS0 = estimateS0
        self.alpha = alpha
        self.forcedBeta = forcedBeta
        self.forcedGamma = forcedGamma
        self.betaBounds = betaBounds
        self.gammaBounds = gammaBounds
        self.S0bounds = S0bounds
        self.hospRate = hospRate
        self.daysToHosp = daysToHosp
        self.hospitalDuration = daysToLeave
        self.opt = opt
        self.R0bounds = R0bounds
        self.adjust_recovered = adjust_recovered

        self.load_data()

        self.end_data = self.confirmed.index.max()

    def load_CSSE(self,
                       dir=".\\COVID-19\\csse_covid_19_data\\csse_covid_19_time_series\\"):

        confirmed = pd.read_csv(dir+"time_series_covid19_confirmed_global.csv")
        confirmed = confirmed.drop(confirmed.columns[[0, 2, 3]], axis=1).set_index('Country/Region').T
        confirmed.index = pd.to_datetime(confirmed.index)
        self.confirmed = confirmed[self.country]


        deaths = pd.read_csv(dir + "time_series_covid19_deaths_global.csv")
        deaths = deaths.drop(deaths.columns[[0, 2, 3]], axis=1).set_index('Country/Region').T
        deaths.index = pd.to_datetime(deaths.index)
        self.fatal = deaths[self.country]

        recovered = pd.read_csv(dir + "time_series_covid19_recovered_global.csv")
        recovered = recovered.drop(recovered.columns[[0, 2, 3]], axis=1).set_index('Country/Region').T
        recovered.index = pd.to_datetime(recovered.index)
        self.recovered = recovered[self.country]

    def load_data(self):
        """
        New function to use our prop data
        """
        self.load_CSSE()

        # Adjust recovered curve
        if self.adjust_recovered:
            self.recovered = self.smoothCurve(self.recovered)

        # Using unreported estimate
        self.confirmed = self.confirmed * self.infectedAssumption
        self.recovered = self.recovered * self.infectedAssumption * self.recoveredAssumption

        # find date in which nth case is reached
        nth_index = self.confirmed[self.confirmed >= self.nth].index[0]

        if not self.quarantineDate:
            self.quarantineDate = self.confirmed.index[-1]
        quarantine_index = pd.Series(False, index=self.confirmed.index)
        quarantine_index[quarantine_index.index >= self.quarantineDate] = True

        self.quarantine_index = quarantine_index.loc[nth_index:]
        self.confirmed = self.confirmed.loc[nth_index:]
        self.fatal = self.fatal.loc[nth_index:]
        self.recovered = self.recovered.loc[nth_index:]

        self.initialize_parameters()

        #True data series
        self.R_actual = self.fatal + self.recovered
        self.I_actual = self.confirmed - self.R_actual

    def smoothCurve(self, df):
        df[df.diff() <= 0] = np.nan
        # df.loc[dt.datetime(2020, 4, 9)] = np.nan
        df.interpolate('linear', inplace=True)
        return df

    def initialize_parameters(self):
        self.R_0 = self.recovered[0] + self.fatal[0]
        self.I_0 = (self.confirmed.iloc[0] - self.R_0)
        self.S_0 = self.N - self.R_0 - self.I_0

    def extend_index(self, index, new_size):

        new_values = pd.date_range(start=index[-1], periods=150)
        new_index = index.join(new_values, how='outer')

        return new_index

    def estimate(self, verbose=True):

        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))

        if not self.forcedBeta:
            betaBounds = self.betaBounds
        else:
            betaBounds = (self.forcedBeta, self.forcedBeta)

        if not self.forcedGamma:
            gammaBounds = self.gammaBounds
        else:
            gammaBounds = (self.forcedGamma, self.forcedGamma)

        optimal = minimize(
            self.loss,
            [0.2, 0.07, ],
            args=(),
            method=self.opt,
            # options={'maxiter' : 5},
            # method='TNC',
            bounds=[betaBounds, gammaBounds, ]
        )
        self.optimizer = optimal
        beta, gamma, = optimal.x
        if verbose:
            print("Beta:{beta} Gamma:{gamma}".format(beta=beta, gamma=gamma, ))
        self.beta = beta
        self.gamma = gamma

        self.R0 = self.beta / self.gamma

        if verbose:
            print('R0:{R0}'.format(R0=self.R0))

    def S0estimate(self, verbose=True):

        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))

        if not self.forcedBeta:
            betaBounds = self.betaBounds
        else:
            betaBounds = (self.forcedBeta, self.forcedBeta)

        if not self.forcedGamma:
            gammaBounds = self.gammaBounds
        else:
            gammaBounds = (self.forcedGamma, self.forcedGamma)

        S0bounds = self.S0bounds

        if self.R0bounds:
            constraints = [
                {'type': 'ineq', 'fun': self.const_lowerBoundR0_S0opt},
                {'type': 'ineq', 'fun': self.const_upperBoundR0_S0opt},
            ]
            self.opt = 'SLSQP'
        else:
            constraints = None


        optimal = minimize(
            self.lossS0,
            [0.2, 0.07, 1e5],
            args=(),
            method=self.opt,
            # options={'maxiter' : 5},
            # method='TNC',
            bounds=[betaBounds, gammaBounds, S0bounds],
            constraints=constraints
        )
        self.optimizer = optimal
        beta, gamma, S_0 = optimal.x
        if verbose:
            print("Beta:{beta} Gamma:{gamma} S_0:{S_0}".format(beta=beta, gamma=gamma, S_0=S_0))
        self.beta = beta
        self.gamma = gamma
        self.S_0 = S_0

        self.R0 = self.beta / self.gamma
        if verbose:
            print('R0:{R0}'.format(R0=self.R0))

    def model(self, t, y):
        S = y[0]
        I = y[1]
        R = y[2]

        ret = [-self.beta_model * S * I / self.N, self.beta_model * S * I / self.N - self.gamma_model * I,
               self.gamma_model * I]
        return ret

    def lossS0(self, point):
        """
        RMSE between actual confirmed cases and the estimated infectious people with given beta and gamma.
        """
        size = self.I_actual.shape[0]
        beta, gamma, S_0 = point

        self.beta_model = beta
        self.gamma_model = gamma

        # solution = solve_ivp(SIR, [0, size], [S_0, self.I_0, self.R_0], t_eval=np.arange(0, size, 1), vectorized=True)
        solution = solve_ivp(self.model, [0, size], [S_0, self.I_0, self.R_0], t_eval=np.arange(0, size, 1), vectorized=True)

        # Put more emphasis on recovered people
        alpha = self.alpha

        l1 = np.sqrt(np.mean((solution.y[1] - self.I_actual) ** 2))
        l2 = np.sqrt(np.mean((solution.y[2] - self.R_actual) ** 2))

        return alpha * l1 + (1 - alpha) * l2

    def loss(self, point):
        """
        RMSE between actual confirmed cases and the estimated infectious people with given beta and gamma.
        """
        size = self.I_actual.shape[0]
        beta, gamma, = point
        self.beta_model = beta
        self.gamma_model = gamma

        solution = solve_ivp(self.model, [0, size], [self.S_0, self.I_0, self.R_0], t_eval=np.arange(0, size, 1), vectorized=True)

        # Put more emphasis on recovered people
        alpha = self.alpha

        l1 = np.sqrt(np.mean((solution.y[1] - self.I_actual) ** 2))
        l2 = np.sqrt(np.mean((solution.y[2] - self.R_actual) ** 2))

        return alpha * l1 + (1 - alpha) * l2

    def predict(self, beta=None, gamma=None, useData = True):
        """
        Predict how the number of people in each compartment can be changed through time toward the future.
        The model is formulated with the given beta and gamma.
        """

        #In case predict function is called with custom parameters
        if not beta:
            beta = self.beta
        if not gamma:
            gamma = self.gamma

        predict_range = self.daysPredict

        # print(self.confirmed.index)
        new_index = self.extend_index(self.confirmed.index, predict_range)

        size = len(new_index)

        self.beta_model = beta
        self.gamma_model = gamma

        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))

        prediction = solve_ivp(self.model, [0, size], [self.S_0, self.I_0, self.R_0],
                               t_eval=np.arange(0, size, 1))

        df = pd.DataFrame({
            'I_Actual': self.I_actual.reindex(new_index),
            'R_Actual': self.R_actual.reindex(new_index),
            'S': prediction.y[0],
            'I': prediction.y[1],
            'R': prediction.y[2]
        }, index=new_index)

        self.df = df
        self.calculateNB()
        print("Predicting with Beta:{beta} Gamma:{gamma}".format(beta=beta, gamma=gamma, ))

    def train(self,):
        """
        Run the optimization to estimate the beta and gamma fitting the given confirmed cases.
        """

        if self.estimateS0:
            self.S0estimate()
        else:
            self.estimate()

        self.predict()

    def calculateNB(self):
        # Need of Beds
        #X% of new cases need hospitalization after n days
        #First tryout is right after new cases bluntly after n days
        self.df['hospDemand'] = ((self.df['S'].shift(1) - self.df['S']) * self.hospRate).shift(self.daysToHosp).copy()
        self.df['hospExpire'] = self.df['hospDemand'].shift(self.hospitalDuration).copy()
        self.df['H'] = (self.df['hospDemand'].cumsum() - self.df['hospExpire'].cumsum()).copy()
        self.df.drop(['hospDemand', 'hospExpire'], axis=1, inplace=True)

    def rollingBetas(self):

        betasList = []
        gammasList = []
        I_actual = self.I_actual.copy()
        R_actual = self.R_actual.copy()
        for date in self.confirmed.index:
            self.I_actual = I_actual.loc[:date]
            self.R_actual = R_actual.loc[:date]
            self.estimate(verbose=False)
            betasList.append(self.beta)
            gammasList.append(self.gamma)

        self.rollingList = pd.DataFrame({'beta': betasList, 'gamma': gammasList})
        self.rollingList.index = self.I_actual.index
        return self.rollingList

############## CONSTRAINT METHODS ################

    def const_lowerBoundR0_S0opt(self, point):
        "constraint has to be R0 > bounds(0) value, thus (R0 - bound) > 0"
        # self.const_lowerBoundR0_S0opt.__code__.co_varnames
        # print(**kwargs)
        # print(locals())
        beta, gamma, S_0 = point
        lowerBound = self.R0bounds[0]
        return (beta/gamma) - lowerBound

    def const_upperBoundR0_S0opt(self, point):
        # print(locals())
        # print(**kwargs)
        # self.const_upperBoundR0_S0opt.__code__.co_varnames
        beta, gamma, S_0 = point
        upperBound = self.R0bounds[0]
        return upperBound - (beta/gamma)


############## VISUALIZATION METHODS ################
    def I_fit_plot(self):
        line_styles = {
            'I_Actual': '--',
            'R_Actual': '--',
        }

        self.df[['I_Actual', 'I']].loc[:self.end_data].plot(style=line_styles)

    def R_fit_plot(self):
        line_styles = {
            'I_Actual': '--',
            'R_Actual': '--',
        }
        self.df[['R_Actual', 'R']].loc[:self.end_data].plot(style=line_styles)

    def main_plot(self):
        fig, ax = plt.subplots(figsize=(15, 10))
        ax.set_title(self.country)
        line_styles ={
            'I_Actual': '--',
            'R_Actual': '--',
        }

        # color = {
        #     'I_Actual': '#FF0000',
        #     # 'R_Actual': '--',
        # }

        self.df.plot(ax=ax, style=line_styles, )

    def rollingPlot(self):
        axes = self.rollingList.plot()
        fig = axes.get_figure()

        axes.axvline(x=self.quarantineDate, color='red', linestyle='--', label='Quarentine')

class SIHRF(SIR):
    """
    This SIR extension split the infected compartiment into non-hospital and hospital cases and the recovered group
    into recovered and fatal

    $$\frac{dS}{dt} = - \frac{\beta IS}{N}$$

    $$\frac{dIN}{dt} = (1 - \rho) \times \frac{\beta IS}{N} - \gamma_{IN} IN$$

    $$\frac{dIH}{dt} = \rho \times \frac{\beta IS}{N} - (1-\delta) \times \gamma_{IH} IH - \delta \times \omega_{IH} IH$$


    $$\frac{dR}{dt} = \gamma_{IN} IN + (1-\delta) \times \gamma_{IH} IH $$

    $$\frac{dF}{dt} = \delta \times \omega_{IH} IH$$
    """
    def __init__(self,
                 gamma_i_bounds=(1/(3*7), 1/(2*7)),
                 gamma_h_bounds=(1/(6*7), 1/(2*7)),
                 omega_bounds=(0.001, 0.1),
                 delta_bounds=(0, 1),
                 alphas=(1/3, 1/3, 1/3),

                 **kwargs):
        self.gamma_i_bounds = gamma_i_bounds
        self.gamma_h_bounds = gamma_h_bounds
        self.omega_bounds = omega_bounds
        self.delta_bounds = delta_bounds
        self.alphas = alphas

        self.rho = kwargs['hospRate']
        super().__init__(**kwargs)


    def model(self, t, y):
        S = y[0]
        I = y[1]
        H = y[2]
        R = y[3]
        F = y[4]

        ret = [
            # S
            -self.beta_model * S * I / self.N,

            # I
            (1 - self.rho) * self.beta_model * S * I / self.N  # (1-rho) BIS/N
            - self.gamma_I_model * I,  # Gamma_I x I

            # H
            self.rho * self.beta_model * S * I / self.N  # rho BIS/N
            - (1 - self.delta_model) * self.gamma_H_model * H  # - (1-delta) gamma_H H
            - self.delta_model * self.omega_model * H,  # - delta omega H,

            # R
            self.gamma_I_model * I  # gamma_I * I
            + (1 - self.delta_model) * self.gamma_H_model * H,  # (1-delta) gamma_H * H

            # F
            self.delta_model * self.omega_model * H,
        ]

        return ret

    def load_data(self):
        """
        New function to use our prop data
        """
        super().load_data()

        #True data series
        self.R_actual = self.recovered
        self.F_actual = self.fatal
        self.I_actual = self.confirmed - self.R_actual - self.F_actual  # obs this is total I

    def S0estimate(self, verbose=True):
        """
        List of parameters to estimate:
        * beta
        * gamma I
        * gamma H
        * omega
        * S0
        * delta
        * maybe I0 has to be initialized or estimated

        Note: gamma bounds are applied to total gamma (gamma_I + (1-delta) gamma_H + delta omega)
        """
        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))
        betaBounds = self.betaBounds
        S0bounds = self.S0bounds
        gamma_i_bounds = self.gamma_i_bounds
        gamma_h_bounds = self.gamma_h_bounds
        omega_bounds = self.omega_bounds
        genericGammaBounds = (0.001, 0.2)
        deltaBounds = self.delta_bounds

        constraints = [
            {'type': 'ineq', 'fun': self.const_lowerBound_R0},
            {'type': 'ineq', 'fun': self.const_upperBound_R0},
            {'type': 'ineq', 'fun': self.const_lowerBound_gamma},
            {'type': 'ineq', 'fun': self.const_upperBound_gamma},
        ]


        optimal = minimize(
            self.loss,
            np.array([
                0.2,  # beta
                .07,  # gamma I
                0.07,  # gamma H
                0.07,  # omega
                1e6,  # S0
                0.05,  # delta
            ]),
            args=(),
            method='SLSQP',
            bounds=[
                betaBounds,
                # genericGammaBounds,
                # genericGammaBounds,
                # genericGammaBounds,
                gamma_i_bounds,
                gamma_h_bounds,
                omega_bounds,
                S0bounds,
                deltaBounds,

            ],
            constraints=constraints,
        )
        self.optimizer = optimal
        beta, gamma_I, gamma_H, omega, S_0, delta = optimal.x
        gamma = gamma_I + (1 - delta) * gamma_H + delta * omega

        if verbose:
            print("Beta:{beta} Gamma:{gamma} S_0:{S_0}".format(beta=beta, gamma=gamma, S_0=S_0))
            print("Beta:{value} ".format(value=beta,))
            print("Gamma:{value} ".format(value=gamma, ))
            print("Gamma I:{value} ".format(value=gamma_I, ))
            print("Gamma H:{value} ".format(value=gamma_H, ))
            print("Omega:{value} ".format(value=omega, ))
            print("S0:{value} ".format(value=S_0, ))
            print("Delta:{value} ".format(value=delta, ))

        self.beta = beta
        self.gamma = gamma
        self.S_0 = S_0
        self.gamma = gamma
        self.gamma_I = gamma_I
        self.gamma_H = gamma_H
        self.omega = omega
        self.delta = delta
        self.R0 = self.beta / self.gamma
        if verbose:
            print('R0:{R0}'.format(R0=self.R0))

    def initialize_parameters(self):
        self.R_0 = self.recovered[0]
        self.F_0 = self.fatal[0]
        self.I_0 = self.confirmed.iloc[0] - self.R_0 - self.F_0
        # self.S_0 = self.N - self.R_0 - self.I_0 - self.F_0
        self.H_0 = self.I_0 * self.rho
        self.I_0 = self.I_0 * (1 - self.rho)

    def loss(self, point):
        """
        RMSE between actual confirmed cases and the estimated infectious people with given beta and gamma.
        """
        size = self.I_actual.shape[0]
        beta, gamma_I, gamma_H, omega, S_0, delta = point

        gamma = gamma_I + (1 - delta) * gamma_H + delta * omega

        self.gamma_model = gamma
        self.beta_model = beta
        self.gamma_I_model = gamma_I
        self.gamma_H_model = gamma_H
        self.omega_model = omega
        self.delta_model = delta

        # solution = solve_ivp(SIR, [0, size], [S_0, self.I_0, self.R_0], t_eval=np.arange(0, size, 1), vectorized=True)
        solution = solve_ivp(self.model, [0, size], [S_0, self.I_0, self.H_0, self.R_0, self.F_0],
                             t_eval=np.arange(0, size, 1), vectorized=True)

        # Put more emphasis on recovered people
        alphas = self.alphas

        l1 = np.sqrt(np.mean(((solution.y[1] + solution.y[2]) - self.I_actual) ** 2))  # note that I_actual is I + H
        l2 = np.sqrt(np.mean((solution.y[3] - self.R_actual) ** 2))
        l3 = np.sqrt(np.mean((solution.y[4] - self.F_actual) ** 2))

        return alphas[0] * l1 + alphas[1] * l2 + alphas[2] * l3

    def predict(self,):
        """
        Predict how the number of people in each compartment can be changed through time toward the future.
        The model is formulated with the given beta and gamma.
        """

        predict_range = self.daysPredict

        # print(self.confirmed.index)
        new_index = self.extend_index(self.confirmed.index, predict_range)

        size = len(new_index)

        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))

        prediction = solve_ivp(self.model, [0, size], [self.S_0, self.I_0, self.H_0, self.R_0, self.F_0],
                             t_eval=np.arange(0, size, 1), vectorized=True)

        df = pd.DataFrame({
            'I_Actual': self.I_actual.reindex(new_index),
            'R_Actual': self.R_actual.reindex(new_index),
            'F_Actual': self.F_actual.reindex(new_index),
            'S': prediction.y[0],
            'IN': prediction.y[1],
            'H': prediction.y[2],
            'R': prediction.y[3],
            'F': prediction.y[4],
            'I': prediction.y[1] + prediction.y[2]
        }, index=new_index)

        self.df = df
############## VISUALIZATION METHODS ################
    def main_plot(self):
        fig, ax = plt.subplots(figsize=(15, 10))
        ax.set_title(self.country)
        line_styles ={
            'I_Actual': '--',
            'R_Actual': '--',
            'F_Actual': '--',
        }

        # color = {
        #     'I_Actual': '#FF0000',
        #     # 'R_Actual': '--',
        # }

        self.df.plot(ax=ax, style=line_styles, )

    def I_plot(self):
        line_styles = {
            'I_Actual': '--',
            'R_Actual': '--',
            'F_Actual': '--',
        }

        self.df[['I_Actual', 'I', 'IN', 'H']].loc[:self.end_data].plot(style=line_styles)

    def H_F_plot(self):
        line_styles = {
            'I_Actual': '--',
            'R_Actual': '--',
            'F_Actual': '--',
        }

        self.df[['H', 'F', 'F_Actual',]].loc[:self.end_data].plot(style=line_styles)

    def F_fit_plot(self):
        line_styles = {
            'I_Actual': '--',
            'R_Actual': '--',
            'F_Actual': '--',
        }

        self.df[['F_Actual', 'F']].loc[:self.end_data].plot(style=line_styles)

    def actuals_plot(self):
        line_styles = {
            'I_Actual': '--',
            'R_Actual': '--',
            'F_Actual': '--',
        }

        self.df[['I_Actual', 'R_Actual', 'F_Actual']].loc[:self.end_data].plot(style=line_styles)

############## CONSTRAINT METHODS ################

    def const_lowerBound_gamma(self, point):
        "constraint has to be R0 > bounds(0) value, thus (R0 - bound) > 0"

        lowerBound = self.gammaBounds[0]

        beta, gamma_I, gamma_H, omega, S0, delta = point

        gamma = gamma_I + (1-delta) * gamma_H + delta * omega

        return gamma - lowerBound

    def const_upperBound_gamma(self, point):

        upperBound = self.gammaBounds[1]

        beta, gamma_I, gamma_H, omega, S0, delta = point

        gamma = gamma_I + (1 - delta) * gamma_H + delta * omega

        return upperBound - gamma

    def const_lowerBound_R0(self, point):
        "constraint has to be R0 > bounds(0) value, thus (R0 - bound) > 0"

        lowerBound = self.R0bounds[0]

        beta, gamma_I, gamma_H, omega, S0, delta = point

        gamma = gamma_I + (1 - delta) * gamma_H + delta * omega
        R0 = beta / gamma
        return R0 - lowerBound

    def const_upperBound_R0(self, point):

        upperBound = self.R0bounds[1]

        beta, gamma_I, gamma_H, omega, S0, delta = point

        gamma = gamma_I + (1 - delta) * gamma_H + delta * omega
        R0 = beta / gamma
        return upperBound - R0





class SEIR(SIR):
    def __init__(self,
                 incubationPeriod = 7,
                 forceE0 = None,
                 **kwargs):
        self.forceE0 = forceE0
        self.incubationPeriod = incubationPeriod
        self.sigma = 1 / incubationPeriod
        super().__init__(**kwargs)


    def initialize_parameters(self):
        ## incubated E_0 = 1/incPeriod * each of the following incPeriod days
        #TODO check
        self.E_0 = int(((self.confirmed - self.recovered - self.fatal).iloc[0:(self.incubationPeriod-1)]).sum() / self.incubationPeriod)
        if self.forceE0:
            self.E_0 = self.forceE0
        self.R_0 = self.recovered[0] + self.fatal[0]
        self.I_0 = (self.confirmed.iloc[0] - self.R_0)
        self.S_0 = self.N - self.R_0 - self.I_0

    def loss(self, point):
        """
        RMSE between actual confirmed cases and the estimated infectious people with given beta and gamma.
        """
        size = self.confirmed.shape[0]
        sigma = self.sigma
        if self.estimateBeta2:
            beta, gamma, beta2 = point
        else:
            beta, gamma = point
            beta2 = beta

        def SIR(t, y):
            S = y[0]
            E = y[1]
            I = y[2]
            R = y[3]

            if t < self.quarantine_loc:
                ret = [-beta * S * I / self.N, beta * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            else:
                ret = [-beta2 * S * I / self.N, beta2 * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            return ret

        solution = solve_ivp(SIR, [0, size], [self.S_0, self.E_0, self.I_0, self.R_0], t_eval=np.arange(0, size, 1), vectorized=True)

        # Put more emphasis on recovered people
        alpha = self.alpha

        l1 = np.sqrt(np.mean((solution.y[2] - self.I_actual) ** 2))
        l2 = np.sqrt(np.mean((solution.y[3] - self.R_actual) ** 2))

        return alpha * l1 + (1 - alpha) * l2

    def predict(self, beta=None, gamma=None):
        """
        Predict how the number of people in each compartment can be changed through time toward the future.
        The model is formulated with the given beta and gamma.
        """

        sigma = self.sigma

        #In case predict function is called with custom parameters
        if not beta:
            beta = self.beta
        if not gamma:
            gamma = self.gamma


        if self.estimateBeta2:
            beta2 = self.beta2
        else:
            beta2 = beta

        if self.estimateBeta3:
            # beta3 = self.beta2
            beta3 = (self.beta2 + self.beta) / 2
        else:
            beta3 = beta

        predict_range = self.daysPredict

        # print(self.confirmed.index)
        new_index = self.extend_index(self.confirmed.index, predict_range)
        # print(new_index) #AQUI JA ESTA ERRADO

        size = len(new_index)

        def SIR(t, y):
            S = y[0]
            E = y[1]
            I = y[2]
            R = y[3]

            if t < self.quarantine_loc:
                ret = [-beta * S * I / self.N, beta * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            else:
                ret = [-beta2 * S * I / self.N, beta2 * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            return ret

        # print(self.backDate)
        # print(new_index.get_loc(self.backDate))
        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))
        self.back_loc = float(new_index.get_loc(self.backDate))

        prediction = solve_ivp(SIR, [0, size], [self.S_0, self.E_0, self.I_0, self.R_0],
                               t_eval=np.arange(0, size, 1))

        df = pd.DataFrame({
            'I_Actual': self.I_actual.reindex(new_index),
            'R_Actual': self.R_actual.reindex(new_index),
            'S': prediction.y[0],
            'E': prediction.y[1],
            'I': prediction.y[2],
            'R': prediction.y[3]
        }, index=new_index)

        # if self.log:
        #     df = df.transform(np.exp)

        self.df = df
        print("Predicting with Beta:{beta} Beta2: {beta2} Gamma:{gamma} Sigma:{sigma}".format(sigma=sigma,beta=beta, gamma=gamma, beta2=beta2))


class LearnerSEIR(object):
    def __init__(self,
                 country='Brazil',
                 N = 200e6,
                 infectedAssumption=1,
                 recoveredAssumption=1,
                 nth=1,
                 daysPredict=150,
                 estimateBeta2 = False,
                 estimateBeta3 = False,
                 quarantineDate = None,
                 backDate = dt.datetime(2020, 4, 22),
                 alpha=0.5,
                 forcedBeta = None,
                 forcedGamma = None,
                 elag=15,
                 ):

        self.country = country
        self.N = N
        self.infectedAssumption = infectedAssumption  # Multiplier to account for non reported
        self.recoveredAssumption = recoveredAssumption
        self.nth = nth  # minimum number of cases to start modelling
        self.daysPredict = daysPredict
        self.quarantineDate = quarantineDate
        self.backDate = backDate
        self.estimateBeta2 = estimateBeta2
        self.estimateBeta3 = estimateBeta3
        self.alpha = alpha
        self.forcedBeta = forcedBeta
        self.forcedGamma = forcedGamma
        self.elag = elag

        self.load_data()

        self.end_data = self.confirmed.index.max()

    def load_CSSE(self,
                       dir=".\\COVID-19\\csse_covid_19_data\\csse_covid_19_time_series\\"):

        confirmed = pd.read_csv(dir+"time_series_covid19_confirmed_global.csv")
        confirmed = confirmed.drop(confirmed.columns[[0, 2, 3]], axis=1).set_index('Country/Region').T
        confirmed.index = pd.to_datetime(confirmed.index)
        self.confirmed = confirmed[self.country]

        deaths = pd.read_csv(dir + "time_series_covid19_deaths_global.csv")
        deaths = deaths.drop(deaths.columns[[0, 2, 3]], axis=1).set_index('Country/Region').T
        deaths.index = pd.to_datetime(deaths.index)
        self.fatal = deaths[self.country]

        recovered = pd.read_csv(dir + "time_series_covid19_recovered_global.csv")
        recovered = recovered.drop(recovered.columns[[0, 2, 3]], axis=1).set_index('Country/Region').T
        recovered.index = pd.to_datetime(recovered.index)
        self.recovered = recovered[self.country]

    def load_data(self):
        """
        New function to use our prop data
        """
        self.load_CSSE()

        # Using unreported estimate
        self.confirmed = self.confirmed * self.infectedAssumption
        self.recovered = self.recovered * self.infectedAssumption * self.recoveredAssumption

        # find date in which nth case is reached
        nth_index = self.confirmed[self.confirmed >= self.nth].index[0]

        if not self.quarantineDate:
            self.quarantineDate=self.confirmed.index[-1]
        quarantine_index = pd.Series(False, index=self.confirmed.index)
        quarantine_index[quarantine_index.index >= self.quarantineDate] = True

        self.quarantine_index = quarantine_index.loc[nth_index:]
        self.confirmed = self.confirmed.loc[nth_index:]
        self.fatal = self.fatal.loc[nth_index:]
        self.recovered = self.recovered.loc[nth_index:]

        #Initial parameters
        self.E_0 = self.confirmed.iloc[self.elag]
        self.R_0 = self.recovered[0] + self.fatal[0]
        self.I_0 = (self.confirmed.iloc[0] - self.R_0)
        self.S_0 = self.N - self.R_0 - self.I_0

        #True data series
        self.R_actual = self.fatal + self.recovered
        self.I_actual = self.confirmed - self.R_actual

    def extend_index(self, index, new_size):

        new_values = pd.date_range(start=index[-1], periods=150)
        new_index = index.join(new_values, how='outer')

        return new_index

    def estimate(self):

        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))

        if not self.forcedBeta:
            betaBounds = (0.00000001, 2.0)
        else:
            betaBounds = (self.forcedBeta, self.forcedBeta)

        if not self.forcedGamma:
            gammaBounds = (0.00000001, 2.0)
        else:
            gammaBounds = (self.forcedGamma, self.forcedGamma)

        sigmaBounds = (0.00001, 2.0)

        if self.estimateBeta2:
            optimal = minimize(
                self.loss,
                [0.001, 0.001, 0.001, 0.001],
                args=(),
                method='L-BFGS-B',
                # options={'maxiter' : 5},
                # method='TNC',
                bounds=[betaBounds, gammaBounds, (0.00000001, 2.0), sigmaBounds]
            )
            self.optimizer = optimal
            beta, gamma, beta2, sigma = optimal.x
            print("Beta:{beta} Beta2: {beta2} Gamma:{gamma} Sigma:{sigma}".format(beta=beta, gamma=gamma, beta2=beta2, sigma=sigma))
            self.beta = beta
            self.gamma = gamma
            self.beta2 = beta2
            self.params =[self.beta, self.gamma, self.beta2, self.sigma]
        else:
            optimal = minimize(
                self.loss,
                [0.1, 0.001, 0.1],
                args=(),
                method='L-BFGS-B',
                # options={'maxiter' : 5},
                # method='TNC',
                bounds=[betaBounds, gammaBounds, sigmaBounds]
            )
            self.optimizer = optimal
            beta, gamma, sigma = optimal.x
            print("Beta:{beta} Gamma:{gamma} Sigma:{sigma}".format(beta=beta, gamma=gamma, sigma=sigma))
            self.beta = beta
            self.gamma = gamma
            self.sigma = sigma
            self.params = [self.beta, self.gamma, self.sigma]

        self.R0 = self.beta / self.gamma
        print('R0:{R0}'.format(R0=self.R0))

    def loss(self, point):
        """
        RMSE between actual confirmed cases and the estimated infectious people with given beta and gamma.
        """
        size = self.confirmed.shape[0]
        if self.estimateBeta2:
            beta, gamma, beta2, sigma = point
        else:
            beta, gamma, sigma = point
            beta2 = beta

        def SIR(t, y):
            S = y[0]
            E = y[1]
            I = y[2]
            R = y[3]

            if t < self.quarantine_loc:
                ret = [-beta * S * I / self.N, beta * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            else:
                ret = [-beta2 * S * I / self.N, beta2 * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            return ret

        solution = solve_ivp(SIR, [0, size], [self.S_0, self.E_0, self.I_0, self.R_0], t_eval=np.arange(0, size, 1), vectorized=True)

        # Put more emphasis on recovered people
        alpha = self.alpha

        l1 = np.sqrt(np.mean((solution.y[2] - self.I_actual) ** 2))
        l2 = np.sqrt(np.mean((solution.y[3] - self.R_actual) ** 2))

        return alpha * l1 + (1 - alpha) * l2

    def predict(self, beta=None, gamma=None):
        """
        Predict how the number of people in each compartment can be changed through time toward the future.
        The model is formulated with the given beta and gamma.
        """

        #In case predict function is called with custom parameters
        if not beta:
            beta = self.beta
        if not gamma:
            gamma = self.gamma


        if self.estimateBeta2:
            beta2 = self.beta2
        else:
            beta2 = beta

        if self.estimateBeta3:
            # beta3 = self.beta2
            beta3 = (self.beta2 + self.beta) / 2
        else:
            beta3 = beta

        sigma = self.sigma

        predict_range = self.daysPredict

        # print(self.confirmed.index)
        new_index = self.extend_index(self.confirmed.index, predict_range)
        # print(new_index) #AQUI JA ESTA ERRADO

        size = len(new_index)

        def SIR(t, y):
            S = y[0]
            E = y[1]
            I = y[2]
            R = y[3]

            if t < self.quarantine_loc:
                ret = [-beta * S * I / self.N, beta * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            else:
                ret = [-beta2 * S * I / self.N, beta2 * S * I / self.N - sigma * E, sigma * E - gamma * I, gamma * I]
            return ret

        # print(self.backDate)
        # print(new_index.get_loc(self.backDate))
        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))
        self.back_loc = float(new_index.get_loc(self.backDate))

        prediction = solve_ivp(SIR, [0, size], [self.S_0, self.E_0, self.I_0, self.R_0],
                                                     t_eval=np.arange(0, size, 1))

        df = pd.DataFrame({
            'I_Actual': self.I_actual.reindex(new_index),
            'R_Actual': self.R_actual.reindex(new_index),
            'S': prediction.y[0],
            'E': prediction.y[1],
            'I': prediction.y[2],
            'R': prediction.y[3]
        }, index=new_index)
        self.df = df
        print("Predicting with Beta:{beta} Beta2: {beta2} Gamma:{gamma} Sigma:{sigma}".format(beta=beta, gamma=gamma,
                                                                                              beta2=beta2,sigma=sigma))

    def predict_linear(self, beta=None, gamma=None):
        """
        Predict how the number of people in each compartment can be changed through time toward the future.
        The model is formulated with the given beta and gamma.
        """

        #In case predict function is called with custom parameters
        if not beta:
            beta = self.beta
        if not gamma:
            gamma = self.gamma


        if self.estimateBeta2:
            beta2 = self.beta2
        else:
            beta2 = beta

        if self.estimateBeta3:
            # beta3 = self.beta2
            beta3 = (self.beta2 + self.beta) / 2
        else:
            beta3 = beta

        predict_range = self.daysPredict

        # print(self.confirmed.index)
        new_index = self.extend_index(self.confirmed.index, predict_range)
        # print(new_index) #AQUI JA ESTA ERRADO

        size = len(new_index)

        def SIR(t, y):
            S = y[0]
            I = y[1]
            R = y[2]

            if t < self.quarantine_loc:
                ret = [(-beta * S * I / self.N) + S, (beta * S * I / self.N - gamma * I) + I, (gamma * I) + R]
            elif t < self.back_loc:
                ret = [(-beta3 * S * I / self.N) + S, (beta3 * S * I / self.N - gamma * I) + I, (gamma * I) + R]
            else:
                ret = [(-beta2 * S * I / self.N) + S, (beta2 * S * I / self.N - gamma * I) + I, (gamma * I) + R]
            return ret

        # print(self.backDate)
        # print(new_index.get_loc(self.backDate))
        self.quarantine_loc = float(self.confirmed.index.get_loc(self.quarantineDate))
        self.back_loc = float(new_index.get_loc(self.backDate))

        #prediction = solve_ivp(SIR, [0, size], [self.S_0, self.I_0, self.R_0],t_eval=np.arange(0, size, 1))
        prediction = np.empty((size, 3))
        prediction[0, :] = [self.S_0, self.I_0, self.R_0]

        for t in range(1, size):
            prediction[t, :] = SIR(t, prediction[t-1, :])


        df = pd.DataFrame({
            'I_Actual': self.I_actual.reindex(new_index),
            'R_Actual': self.R_actual.reindex(new_index),
            'S': prediction[:, 0],
            'I': prediction[:, 1],
            'R': prediction[:, 2]
        }, index=new_index)
        self.df = df
        print("Predicting with Beta:{beta} Beta2: {beta2} Gamma:{gamma}".format(beta=beta, gamma=gamma, beta2=beta2))

    def train(self):
        """
        Run the optimization to estimate the beta and gamma fitting the given confirmed cases.
        """

        self.estimate()

        self.predict()

        # fig, ax = plt.subplots(figsize=(15, 10))
        # ax.set_title(self.country)
        # self.df.plot(ax=ax)
        # fig.savefig(f"{self.country}.png")

############## VISUALIZATION METHODS ################
    def I_fit_plot(self):
        self.df[['I_Actual', 'I']].loc[:self.end_data].plot()

    def R_fit_plot(self):
        self.df[['R_Actual', 'R']].loc[:self.end_data].plot()

    def main_plot(self):
        fig, ax = plt.subplots(figsize=(15, 10))
        ax.set_title(self.country)
        self.df.plot(ax=ax)



if __name__ == '__main__':
    # jap = SIR('Brazil',
    #               N=200e6,
    #               alpha=0.7,
    #               betaBounds=(0.1, 0.3),
    #               gammaBounds=(0.05, 0.1),
    #               )
    # jap.train()

    # jap = SEIR(country='Brazil',
    #           N=200e6,
    #           alpha=0.7,
    #           betaBounds=(0.1, 0.3),
    #           gammaBounds=(0.05, 0.1),
    #           )
    # jap.train()

    t1 = SIR('Brazil',
             N=1e6,
             # N=1e6,
             alpha=1,
             betaBounds=(0.1, 1.5),
             gammaBounds=(0.05, 2.0),
             S0bounds=(200e6 * .01, 200e6 * .12),
             nth=100,
             hospRate=0.10,
             daysToHosp=4,  # big for detction
             daysToLeave=12,
             infectedAssumption=1,
             # forcedBeta = 3,
             # quarantineDate = dt.datetime(2020,3,16), #italy lockdown was on the 9th
             # estimateBeta2 = True
             estimateS0=True,
             # opt='SLSQP',
             R0bounds=(2.0, 3.0),
             adjust_recovered=True,

             )

    t1.train()
