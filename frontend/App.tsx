import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaView, StyleSheet } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';

import HomeScreen from './src/screens/HomeScreen';
import ExploreScreen from './src/screens/ExploreScreen';
import ReflectScreen from './src/screens/ReflectScreen';
import ProfileScreen from './src/screens/ProfileScreen';
import { colors } from './src/theme/colors';

export type RootTabParamList = {
  Growth: undefined;
  Explore: undefined;
  Reflect: undefined;
  Profile: undefined;
};

const Tab = createBottomTabNavigator<RootTabParamList>();

export default function App() {
  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />
      <NavigationContainer>
        <Tab.Navigator
          screenOptions={{
            tabBarStyle: {
              backgroundColor: colors.background,
              borderTopColor: colors.surface,
            },
            tabBarActiveTintColor: colors.primary,
            tabBarInactiveTintColor: colors.textSecondary,
            headerShown: false,
          }}
        >
          <Tab.Screen name="Growth" component={HomeScreen} />
          <Tab.Screen name="Explore" component={ExploreScreen} />
          <Tab.Screen name="Reflect" component={ReflectScreen} />
          <Tab.Screen name="Profile" component={ProfileScreen} />
        </Tab.Navigator>
      </NavigationContainer>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
});
